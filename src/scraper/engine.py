from __future__ import annotations

import logging

from .base import BaseScraper, Response
from .curl_scraper import CurlScraper

logger = logging.getLogger(__name__)

CHALLENGE_SIGNS = ["challenge-platform", "cf-browser-verification", "Just a moment", "cf_chl_opt"]
WAF_SIGNS = ["您的連線暫時異常", "Connection is temporarily unavailable"]

# If page has challenge markers BUT body is large, it's a real page with residual CF scripts
CHALLENGE_MAX_BODY_SIZE = 150000


def is_waf_blocked(resp: Response) -> bool:
    """Detect WAF rate-limit error page from target site."""
    return any(sign in resp.text[:3000] for sign in WAF_SIGNS)


def _is_challenged(resp: Response) -> bool:
    """Detect responses that need browser fallback.

    Triggers on:
    - HTTP 403 (blocked)
    - HTTP 4xx/5xx with empty body (e.g. 484 from 博客來)
    - Cloudflare challenge markers in small pages
    - WAF rate-limit error pages
    """
    if resp.status_code == 403:
        return True
    # Non-200 with empty or tiny body — likely needs browser
    if resp.status_code != 200 and len(resp.text) < 1000:
        return True
    return _is_challenged_content(resp)


def _is_challenged_content(resp: Response) -> bool:
    """Check response content only (ignore status code).

    Used to validate browser fallback results where Playwright may report
    the initial navigation status (e.g. 403) even after challenge is resolved.
    """
    # CF challenge markers in small page
    has_signs = any(sign in resp.text for sign in CHALLENGE_SIGNS)
    if has_signs and len(resp.text) < CHALLENGE_MAX_BODY_SIZE:
        return True
    # WAF rate-limit page
    if is_waf_blocked(resp):
        return True
    return False


class ScraperEngine:
    """Layered scraper engine — tries curl_cffi first, falls back to browser on challenge."""

    def __init__(self, *, use_browser: bool = True):
        self._use_browser = use_browser
        self._scrapers: list[BaseScraper] = []

    async def _ensure_scrapers(self):
        if not self._scrapers:
            self._scrapers.append(CurlScraper())
            if self._use_browser:
                try:
                    from .browser_pool import BrowserPool
                    self._scrapers.append(BrowserPool())
                except ImportError:
                    logger.warning("Playwright not installed, browser fallback disabled")

    async def _request(self, method: str, url: str, **kwargs) -> Response:
        await self._ensure_scrapers()
        last_error: Exception | None = None

        for scraper in self._scrapers:
            try:
                func = scraper.get if method == "GET" else scraper.post
                resp = await func(url, **kwargs)

                if _is_challenged(resp):
                    logger.warning(
                        "%s got Cloudflare challenge for %s, trying next scraper",
                        type(scraper).__name__, url,
                    )
                    last_error = Exception(f"Cloudflare challenge from {url}")
                    continue

                logger.info("%s %s → %d via %s", method, url, resp.status_code, type(scraper).__name__)
                return resp
            except Exception as e:
                logger.warning("%s failed: %s", type(scraper).__name__, e)
                last_error = e
                continue

        raise last_error or Exception("No scrapers available")

    async def get(self, url: str, **kwargs) -> Response:
        return await self._request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> Response:
        return await self._request("POST", url, **kwargs)

    async def close(self):
        for s in self._scrapers:
            await s.close()
        self._scrapers.clear()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
