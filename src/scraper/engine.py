from __future__ import annotations

import logging

from .base import BaseScraper, Response
from .curl_scraper import CurlScraper

logger = logging.getLogger(__name__)


class ScraperEngine:
    """Layered scraper engine — tries curl_cffi first, falls back to browser if needed."""

    def __init__(self):
        self._scrapers: list[BaseScraper] = []

    async def _ensure_scrapers(self):
        if not self._scrapers:
            self._scrapers.append(CurlScraper())
            # Browser scraper added here in the future as fallback

    async def get(self, url: str, **kwargs) -> Response:
        await self._ensure_scrapers()
        last_error: Exception | None = None
        for scraper in self._scrapers:
            try:
                resp = await scraper.get(url, **kwargs)
                if resp.status_code == 403:
                    logger.warning("%s returned 403, trying next scraper", type(scraper).__name__)
                    last_error = Exception(f"403 from {url}")
                    continue
                return resp
            except Exception as e:
                logger.warning("%s failed: %s", type(scraper).__name__, e)
                last_error = e
                continue
        raise last_error or Exception("No scrapers available")

    async def post(self, url: str, **kwargs) -> Response:
        await self._ensure_scrapers()
        last_error: Exception | None = None
        for scraper in self._scrapers:
            try:
                resp = await scraper.post(url, **kwargs)
                if resp.status_code == 403:
                    logger.warning("%s returned 403, trying next scraper", type(scraper).__name__)
                    last_error = Exception(f"403 from {url}")
                    continue
                return resp
            except Exception as e:
                logger.warning("%s failed: %s", type(scraper).__name__, e)
                last_error = e
                continue
        raise last_error or Exception("No scrapers available")

    async def close(self):
        for s in self._scrapers:
            await s.close()
        self._scrapers.clear()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
