from __future__ import annotations

import logging

from curl_cffi.requests import AsyncSession

from ..config.settings import get as cfg
from .base import BaseScraper, Response

logger = logging.getLogger(__name__)

def _detect_impersonate() -> str:
    """Auto-detect impersonate version from system Chrome, fallback to config."""
    configured = cfg("scraper.impersonate", "auto")
    if configured != "auto":
        return configured
    try:
        import subprocess
        from curl_cffi.requests import Session
        result = subprocess.run(
            ["google-chrome", "--version"], capture_output=True, text=True, timeout=5
        )
        version = int(result.stdout.strip().split()[-1].split(".")[0])
        # Try detected version, fallback to lower versions if not supported
        for v in [version, 136, 131, 124, 120]:
            imp = f"chrome{v}"
            try:
                s = Session(impersonate=imp)
                s.get("https://example.com", timeout=5)
                s.close()
                logger.info("Auto-detected impersonate: %s (system Chrome: %d)", imp, version)
                return imp
            except Exception as e:
                if "not supported" in str(e).lower():
                    logger.debug("Impersonate %s not supported, trying next", imp)
                    continue
                # Other errors (network etc) — version is supported
                logger.info("Auto-detected impersonate: %s (system Chrome: %d)", imp, version)
                return imp
        logger.warning("No supported impersonate version found, using chrome136")
        return "chrome136"
    except Exception:
        logger.info("Chrome not found, using default impersonate: chrome136")
        return "chrome136"


DEFAULT_IMPERSONATE = _detect_impersonate()
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
