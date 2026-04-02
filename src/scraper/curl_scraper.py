from __future__ import annotations

import logging

from curl_cffi.requests import AsyncSession

from ..config.settings import get as cfg
from .base import BaseScraper, Response

logger = logging.getLogger(__name__)

DEFAULT_IMPERSONATE = cfg("scraper.impersonate", "chrome136")
DEFAULT_TIMEOUT = cfg("scraper.timeout", 30)


class CurlScraper(BaseScraper):
    """curl_cffi based scraper — TLS fingerprint impersonation to bypass Bot Fight Mode."""

    def __init__(self, *, impersonate: str = DEFAULT_IMPERSONATE, timeout: int = DEFAULT_TIMEOUT):
        self._session = AsyncSession(impersonate=impersonate, timeout=timeout)

    def _wrap(self, r) -> Response:
        return Response(
            status_code=r.status_code,
            text=r.text,
            headers=dict(r.headers),
            url=str(r.url),
        )

    async def get(self, url: str, *, headers: dict | None = None, params: dict | None = None) -> Response:
        logger.debug("GET %s", url)
        r = await self._session.get(url, headers=headers, params=params)
        return self._wrap(r)

    async def post(self, url: str, *, headers: dict | None = None, data: dict | None = None, json: dict | None = None) -> Response:
        logger.debug("POST %s", url)
        r = await self._session.post(url, headers=headers, data=data, json=json)
        return self._wrap(r)

    async def close(self) -> None:
        await self._session.close()
