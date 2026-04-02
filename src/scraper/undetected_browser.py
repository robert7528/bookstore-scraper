"""Undetected Chrome browser — bypasses Cloudflare Turnstile.

Uses undetected-chromedriver which patches Chrome to avoid CDP detection.
Unlike Playwright, Turnstile cannot detect this as automated.

Runs Chrome in a background thread (sync selenium) and bridges to async.
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from ..config.settings import get as cfg
from .base import BaseScraper, Response

logger = logging.getLogger(__name__)

CHALLENGE_TIMEOUT = cfg("browser.challenge_timeout", 15)
CHALLENGE_SIGNS = ["challenge-platform", "cf-browser-verification", "Just a moment"]

_executor = ThreadPoolExecutor(max_workers=cfg("browser.max_tabs", 3))


class UndetectedBrowser(BaseScraper):
    """Browser scraper using undetected-chromedriver to bypass CF Turnstile."""

    def __init__(self, *, timeout: int = 30):
        self._timeout = timeout
        self._driver = None
        self._lock = asyncio.Lock()
        self._last_used: float = 0
        self._idle_timeout = cfg("browser.idle_timeout", 300)
        self._idle_task: asyncio.Task | None = None

    def _ensure_driver_sync(self):
        """Create Chrome driver (must run in thread — selenium is sync)."""
        if self._driver is not None:
            try:
                _ = self._driver.title
                return
            except Exception:
                self._driver = None

        import undetected_chromedriver as uc

        options = uc.ChromeOptions()
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--lang=zh-TW")

        headless = cfg("browser.headless", True)
        if headless:
            options.add_argument("--headless=new")

        self._driver = uc.Chrome(options=options, version_main=None)
        self._driver.set_page_load_timeout(self._timeout)
        logger.info("Undetected Chrome started (headless=%s)", headless)

    def _get_page_sync(self, url: str) -> Response:
        """Fetch URL with challenge handling (sync, runs in thread)."""
        self._ensure_driver_sync()
        driver = self._driver

        driver.get(url)
        self._last_used = time.time()

        # Check for challenge
        if not any(sign in driver.page_source[:5000] for sign in CHALLENGE_SIGNS):
            return Response(
                status_code=200,
                text=driver.page_source,
                headers={},
                url=driver.current_url,
            )

        logger.info("Cloudflare challenge detected, waiting for resolution...")

        # Wait for challenge to resolve (Turnstile auto-solves with undetected-chromedriver)
        deadline = time.time() + CHALLENGE_TIMEOUT
        while time.time() < deadline:
            time.sleep(1)
            title = driver.title
            source = driver.page_source[:5000]

            # Check if challenge resolved
            if "Just a moment" not in title and "<title>Just a moment...</title>" not in source:
                logger.info("Challenge resolved, title: %s", title[:60])
                return Response(
                    status_code=200,
                    text=driver.page_source,
                    headers={},
                    url=driver.current_url,
                )

        # Timeout — save debug screenshot
        logger.warning("Challenge wait timed out after %ds", CHALLENGE_TIMEOUT)
        try:
            driver.save_screenshot("/tmp/challenge_debug.png")
            logger.info("Debug screenshot saved: /tmp/challenge_debug.png")
        except Exception:
            pass

        return Response(
            status_code=403,
            text=driver.page_source,
            headers={},
            url=driver.current_url,
        )

    def _close_sync(self):
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None
            logger.info("Undetected Chrome stopped")

    async def get(self, url: str, *, headers: dict | None = None, params: dict | None = None) -> Response:
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(_executor, partial(self._get_page_sync, url))
        self._last_used = time.time()
        self._start_idle_watcher()
        return resp

    async def post(self, url: str, *, headers: dict | None = None, data: dict | None = None, json: dict | None = None) -> Response:
        # POST not supported — return error
        return Response(status_code=0, text="POST not supported in undetected browser", headers={}, url=url)

    async def close(self) -> None:
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_executor, self._close_sync)

    def _start_idle_watcher(self):
        if self._idle_task and not self._idle_task.done():
            return

        async def _watch():
            while True:
                await asyncio.sleep(60)
                if self._driver and time.time() - self._last_used > self._idle_timeout:
                    logger.info("Undetected Chrome idle for %ds, shutting down", self._idle_timeout)
                    await self.close()
                    break

        self._idle_task = asyncio.create_task(_watch())
