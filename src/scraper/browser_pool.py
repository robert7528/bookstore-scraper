"""Browser pool — single Chromium instance, controlled tab concurrency.

Keeps one browser process alive, opens/closes pages (tabs) per request.
Avoids the ~1-2s startup cost of launching Chromium for every challenge.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time

from playwright.async_api import async_playwright, Browser, BrowserContext

from ..config.settings import get as cfg
from .base import BaseScraper, Response

logger = logging.getLogger(__name__)

CHALLENGE_TIMEOUT = cfg("browser.challenge_timeout", 8) * 1000  # ms
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

    def __init__(self, *, headless: bool = cfg("browser.headless", True), timeout: int = 30,
                 max_tabs: int = DEFAULT_MAX_TABS, idle_timeout: int = DEFAULT_IDLE_TIMEOUT,
                 channel: str = cfg("browser.channel", "auto")):
        self._headless = headless
        self._channel = channel
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
        self._warm_pages: dict[str, any] = {}  # domain → page (reuse for CF session)

    def _resolve_channel(self) -> str | None:
        """Resolve browser channel: auto-detect or use configured value."""
        ch = self._channel.lower()
        if ch == "chromium":
            return None  # Playwright built-in Chromium
        if ch == "chrome":
            return "chrome"
        # auto: prefer Chrome if installed, fallback to Chromium
        if shutil.which("google-chrome") or shutil.which("google-chrome-stable"):
            logger.info("Auto-detected system Google Chrome")
            return "chrome"
        logger.info("System Chrome not found, using Playwright Chromium")
        return None

    async def _ensure_browser(self):
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                channel = self._resolve_channel()
                launch_kwargs = dict(
                    headless=self._headless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-features=IsolateOrigins,site-per-process",
                        "--no-first-run",
                        "--no-default-browser-check",
                    ],
                )
                if channel:
                    launch_kwargs["channel"] = channel
                self._browser = await self._playwright.chromium.launch(**launch_kwargs)
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
            self._warm_pages.clear()
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
            # Try to click Turnstile checkbox if present
            try:
                # Wait for Turnstile iframe to appear
                await asyncio.sleep(2)
                # Find all iframes from Cloudflare challenges
                for frame in page.frames:
                    if "challenges.cloudflare.com" in (frame.url or ""):
                        # Click the checkbox area inside the Turnstile iframe
                        cb = frame.locator("body")
                        if await cb.count() > 0:
                            await cb.click(timeout=3000)
                            logger.info("Clicked Turnstile checkbox in iframe: %s", frame.url[:80])
                            await asyncio.sleep(3)
                            break
                else:
                    # No iframe found — try clicking the widget area on the main page
                    widget = page.locator("[id*='turnstile'], [class*='cf-turnstile']")
                    if await widget.count() > 0:
                        await widget.first.click(timeout=3000)
                        logger.info("Clicked Turnstile widget on main page")
                        await asyncio.sleep(3)
            except Exception as ce:
                logger.debug("Turnstile click attempt: %s", ce)

            # Wait for CF challenge to resolve and navigate to the real page.
            await page.wait_for_function(
                """() => {
                    if (document.title === 'Just a moment...') return false;
                    const html = document.documentElement.innerHTML;
                    const signs = ['challenge-platform', 'cf-browser-verification', 'cf_chl_opt'];
                    const hasSign = signs.some(s => html.includes(s));
                    if (hasSign && html.length < 150000) return false;
                    return true;
                }""",
                timeout=CHALLENGE_TIMEOUT,
            )
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
            logger.info("Challenge resolved, page title: %s", await page.title())
        except Exception as e:
            logger.warning("Challenge wait timed out: %s", e)
            # Save debug screenshot
            try:
                from pathlib import Path
                debug_dir = Path(cfg("server.debug_dir", "/tmp"))
                screenshot_path = debug_dir / "challenge_debug.png"
                await page.screenshot(path=str(screenshot_path))
                logger.info("Debug screenshot saved: %s", screenshot_path)
            except Exception as se:
                logger.warning("Failed to save debug screenshot: %s", se)

    async def get_cookies(self) -> list[dict]:
        """Get all cookies from the browser context."""
        if self._context:
            return await self._context.cookies()
        return []

    async def _get_warm_page(self, domain: str):
        """Reuse a page that already passed CF challenge for this domain."""
        if domain in self._warm_pages:
            page = self._warm_pages[domain]
            if not page.is_closed():
                return page, False
            del self._warm_pages[domain]
        page = await self._context.new_page()
        return page, True

    async def get(self, url: str, *, headers: dict | None = None, params: dict | None = None) -> Response:
        await self._ensure_browser()
        from urllib.parse import urlparse
        domain = urlparse(url).netloc

        async with self._semaphore:
            page, is_new = await self._get_warm_page(domain)
            if headers and is_new:
                await page.set_extra_http_headers(headers)
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=self._timeout)
                await self._wait_for_challenge(page)
                body = await page.content()
                # Keep page alive for reuse (CF session stays valid)
                self._warm_pages[domain] = page
                return Response(
                    status_code=resp.status if resp else 0,
                    text=body,
                    headers={},
                    url=page.url,
                )
            except Exception:
                await page.close()
                self._warm_pages.pop(domain, None)
                raise
            finally:
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
