"""Per-domain rate limiter to avoid WAF blocking from target sites."""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlparse

from .config.settings import get as cfg

logger = logging.getLogger(__name__)

# Default minimum interval between requests to the same domain (seconds)
DEFAULT_INTERVAL = cfg("scraper.rate_limit_interval", 1.0)


class DomainRateLimiter:
    """Ensures minimum interval between requests to the same domain."""

    def __init__(self, default_interval: float = DEFAULT_INTERVAL):
        self._default_interval = default_interval
        self._last_request: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_domain(self, url: str) -> str:
        try:
            return urlparse(url).netloc
        except Exception:
            return url

    async def wait(self, url: str) -> float:
        """Wait if needed to respect rate limit. Returns actual wait time."""
        domain = self._get_domain(url)

        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()

        async with self._locks[domain]:
            now = time.time()
            last = self._last_request.get(domain, 0)
            elapsed = now - last
            wait_time = 0.0

            if elapsed < self._default_interval:
                wait_time = self._default_interval - elapsed
                logger.debug("Rate limit: waiting %.2fs for %s", wait_time, domain)
                await asyncio.sleep(wait_time)

            self._last_request[domain] = time.time()
            return wait_time


# Global instance
rate_limiter = DomainRateLimiter()
