from __future__ import annotations

import logging

from playwright.async_api import async_playwright, Browser, BrowserContext

from .base import BaseScraper, Response

logger = logging.getLogger(__name__)

# Max time to wait for Cloudflare challenge to resolve (ms)
CHALLENGE_TIMEOUT = 30000
# Signs that a Cloudflare challenge is present
CHALLENGE_SIGNS = ["challenge-platform", "cf-browser-verification", "Just a moment"]


class BrowserScraper(BaseScraper):
    """Playwright-based scraper — real browser to pass Cloudflare Managed Challenge."""

    def __init__(self, *, headless: bool = True, timeout: int = 30):
        self._headless = headless
        self._timeout = timeout * 1000  # playwright uses ms
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def _ensure_browser(self):
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self._headless)
            self._context = await self._browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
            )
            logger.info("Browser launched (headless=%s)", self._headless)

    async def _wait_for_challenge(self, page) -> None:
        """Wait for Cloudflare challenge to resolve."""
        content = await page.content()
        is_challenge = any(sign in content for sign in CHALLENGE_SIGNS)

        if not is_challenge:
            return

        logger.info("Cloudflare challenge detected, waiting for resolution...")
        try:
            # Wait until the challenge script is gone and real content loads
            await page.wait_for_function(
                """() => {
                    return !document.querySelector('script[src*="challenge-platform"]')
                        && document.querySelector('body').innerText.length > 500;
                }""",
                timeout=CHALLENGE_TIMEOUT,
            )
            logger.info("Challenge resolved")
        except Exception:
            # Fallback: just wait a fixed time
            logger.warning("Challenge wait timed out, trying to proceed anyway")

    async def get(self, url: str, *, headers: dict | None = None, params: dict | None = None) -> Response:
        await self._ensure_browser()
        page = await self._context.new_page()

        if headers:
            await page.set_extra_http_headers(headers)

        try:
            logger.debug("Browser GET %s", url)
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=self._timeout)
            await self._wait_for_challenge(page)
            body = await page.content()
            status = resp.status if resp else 0

            return Response(
                status_code=status,
                text=body,
                headers={},
                url=page.url,
            )
        finally:
            await page.close()

    async def post(self, url: str, *, headers: dict | None = None, data: dict | None = None, json: dict | None = None) -> Response:
        await self._ensure_browser()
        page = await self._context.new_page()

        if headers:
            await page.set_extra_http_headers(headers)

        try:
            # Playwright doesn't have a direct POST navigation,
            # use fetch API inside the page
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
            # Navigate first to set origin
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

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
            logger.info("Browser closed")
