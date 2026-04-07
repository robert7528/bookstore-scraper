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

_executor = ThreadPoolExecutor(max_workers=cfg("browser.max_workers", 1))


class UndetectedBrowser(BaseScraper):
    """Browser scraper using undetected-chromedriver to bypass CF Turnstile."""

    def __init__(self, *, timeout: int = 30):
        self._timeout = timeout
        self._driver = None
        self._lock = asyncio.Lock()
        self._last_used: float = 0
        self._created_at: float = 0
        self._idle_timeout = cfg("browser.idle_timeout", 300)
        self._max_lifetime = cfg("browser.max_lifetime", 7200)
        self._idle_task: asyncio.Task | None = None
        self._request_count: int = 0

    def _ensure_driver_sync(self):
        """Create Chrome driver (must run in thread — selenium is sync)."""
        if self._driver is not None:
            # Check if max lifetime exceeded
            if self._max_lifetime > 0 and time.time() - self._created_at > self._max_lifetime:
                logger.info("Chrome max lifetime (%ds) exceeded, restarting (requests served: %d)",
                            self._max_lifetime, self._request_count)
                self._close_sync()
            else:
                try:
                    _ = self._driver.title
                    return
                except Exception:
                    logger.warning("Chrome driver lost, restarting")
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

        # Auto-detect Chrome version to match chromedriver
        import subprocess
        try:
            result = subprocess.run(
                ["google-chrome", "--version"], capture_output=True, text=True, timeout=5
            )
            version_main = int(result.stdout.strip().split()[-1].split(".")[0])
            logger.info("Detected Chrome version: %d", version_main)
        except Exception:
            version_main = None

        self._driver = uc.Chrome(options=options, version_main=version_main)
        self._driver.set_page_load_timeout(self._timeout)
        self._created_at = time.time()
        self._request_count = 0
        logger.info("Undetected Chrome started (headless=%s, max_lifetime=%ds)", headless, self._max_lifetime)

    def _get_page_sync(self, url: str) -> Response:
        """Fetch URL with challenge handling (sync, runs in thread)."""
        self._ensure_driver_sync()
        self._request_count += 1
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
            pid = None
            try:
                pid = self._driver.browser_pid
            except Exception:
                pass
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None
            # Kill entire Chrome process tree and reap zombies
            import os, signal, subprocess
            if pid:
                try:
                    subprocess.run(["pkill", "-9", "-P", str(pid)], capture_output=True, timeout=5)
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
                try:
                    os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    pass
            # Cleanup orphaned processes
            try:
                subprocess.run(["pkill", "-9", "-f", "undetected_chromedriver"], capture_output=True, timeout=5)
                subprocess.run(["pkill", "-9", "-f", "--user-data-dir=/tmp/tmp"], capture_output=True, timeout=5)
            except Exception:
                pass
            # Reap any zombie children
            try:
                while True:
                    p, _ = os.waitpid(-1, os.WNOHANG)
                    if p == 0:
                        break
            except ChildProcessError:
                pass
            logger.info("Undetected Chrome stopped (requests served: %d)", self._request_count)

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
                if not self._driver:
                    break
                now = time.time()
                # Idle timeout
                if now - self._last_used > self._idle_timeout:
                    logger.info("Undetected Chrome idle for %ds, shutting down (requests served: %d)",
                                self._idle_timeout, self._request_count)
                    await self.close()
                    break
                # Max lifetime
                if self._max_lifetime > 0 and now - self._created_at > self._max_lifetime:
                    logger.info("Undetected Chrome max lifetime (%ds) reached, shutting down (requests served: %d)",
                                self._max_lifetime, self._request_count)
                    await self.close()
                    break

        self._idle_task = asyncio.create_task(_watch())
