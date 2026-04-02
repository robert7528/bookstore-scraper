"""Browser pool — single Chromium instance, controlled tab concurrency.

Keeps one browser process alive, opens/closes pages (tabs) per request.
Avoids the ~1-2s startup cost of launching Chromium for every challenge.
"""
from __future__ import annotations

import asyncio
import logging
import time

from playwright.async_api import async_playwright, Browser, BrowserContext

from ..config.settings import get as cfg
from .base import BaseScraper, Response

logger = logging.getLogger(__name__)

CHALLENGE_TIMEOUT = 30000
CHALLENGE_SIGNS = ["challenge-platform", "cf-browser-verification", "Just a moment"]

# Defaults (overridable via configs/settings.yaml)
DEFAULT_MAX_TABS = cfg("browser.max_tabs", 3)
DEFAULT_IDLE_TIMEOUT = cfg("browser.idle_timeout", 300)  # seconds


class BrowserPool(BaseScraper):
    """Pooled browser — one Chromium, concurrent tabs with limit.

    - Browser starts on first request, stays alive for reuse
    - Each request opens a new tab, closes it when done
    - Semaphore limits concurrent tabs to prevent CPU/RAM spikes
    - Auto-shutdown after idle timeout
    """

    def __init__(self, *, headless: bool = True, timeout: int = 30,
                 max_tabs: int = DEFAULT_MAX_TABS, idle_timeout: int = DEFAULT_IDLE_TIMEOUT):
        self._headless = headless
        self._timeout = timeout * 1000
        self._max_tabs = max_tabs
        self._idle_timeout = idle_timeout
        self._semaphore = asyncio.Semaphore(max_tabs)
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._last_used: float = 0
        self._lock = asyncio.Lock()
        self._idle_task: asyncio.Task | None = None

    async def _ensure_browser(self):
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=self._headless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-features=IsolateOrigins,site-per-process",
                        "--no-first-run",
                        "--no-default-browser-check",
                    ],
                )
                self._context = await self._browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                )
                # Hide webdriver property to avoid headless detection
                await self._context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['zh-TW', 'zh', 'en-US', 'en']});
                    window.chrome = {runtime: {}};
                """)
                logger.info("Browser pool started (headless=%s, max_tabs=%d)", self._headless, self._max_tabs)
                self._start_idle_watcher()

            self._last_used = time.time()

    def _start_idle_watcher(self):
        if self._idle_task and not self._idle_task.done():
            return

        async def _watch():
            while True:
                await asyncio.sleep(60)
                if self._browser and time.time() - self._last_used > self._idle_timeout:
                    logger.info("Browser idle for %ds, shutting down", self._idle_timeout)
                    await self._shutdown_browser()
                    break

        self._idle_task = asyncio.create_task(_watch())

    async def _shutdown_browser(self):
        async with self._lock:
            if self._context:
                await self._context.close()
                self._context = None
            if self._browser:
                await self._browser.close()
                self._browser = None
                logger.info("Browser pool stopped")

    async def _wait_for_challenge(self, page) -> None:
        content = await page.content()
        if not any(sign in content for sign in CHALLENGE_SIGNS):
            return

        logger.info("Cloudflare challenge detected, waiting...")
        try:
            # Wait for CF challenge to resolve and navigate to the real page.
            # CF typically redirects after challenge is solved, so we wait for
            # the title to change from "Just a moment..." to something else.
            await page.wait_for_function(
                """() => {
                    // Still on challenge page
                    if (document.title === 'Just a moment...') return false;
                    // Challenge signs still present in small page
                    const html = document.documentElement.innerHTML;
                    const signs = ['challenge-platform', 'cf-browser-verification', 'cf_chl_opt'];
                    const hasSign = signs.some(s => html.includes(s));
                    if (hasSign && html.length < 150000) return false;
                    return true;
                }""",
                timeout=CHALLENGE_TIMEOUT,
            )
            # Wait a bit for page to fully load after redirect
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
            logger.info("Challenge resolved, page title: %s", await page.title())
        except Exception as e:
            logger.warning("Challenge wait timed out: %s", e)

    async def get(self, url: str, *, headers: dict | None = None, params: dict | None = None) -> Response:
        await self._ensure_browser()
        async with self._semaphore:
            page = await self._context.new_page()
            if headers:
                await page.set_extra_http_headers(headers)
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=self._timeout)
                await self._wait_for_challenge(page)
                body = await page.content()
                return Response(
                    status_code=resp.status if resp else 0,
                    text=body,
                    headers={},
                    url=page.url,
                )
            finally:
                await page.close()
                self._last_used = time.time()

    async def post(self, url: str, *, headers: dict | None = None, data: dict | None = None, json: dict | None = None) -> Response:
        await self._ensure_browser()
        async with self._semaphore:
            page = await self._context.new_page()
            if headers:
                await page.set_extra_http_headers(headers)
            try:
                fetch_script = f"""
                    async () => {{
                        const resp = await fetch("{url}", {{
                            method: "POST",
                            headers: {{"Content-Type": "application/x-www-form-urlencoded"}},
                            body: `{data if isinstance(data, str) else ''}`,
                        }});
                        return {{
                            status: resp.status,
                            body: await resp.text(),
                            url: resp.url,
                        }};
                    }}
                """
                await page.goto(url.rsplit("/", 1)[0] or url, wait_until="domcontentloaded", timeout=self._timeout)
                result = await page.evaluate(fetch_script)
                return Response(
                    status_code=result.get("status", 0),
                    text=result.get("body", ""),
                    headers={},
                    url=result.get("url", url),
                )
            finally:
                await page.close()
                self._last_used = time.time()

    async def close(self) -> None:
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        await self._shutdown_browser()
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
