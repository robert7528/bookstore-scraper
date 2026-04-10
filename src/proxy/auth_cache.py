"""Clarivate auth cookie cache — obtains JCR auth via browser IP detection.

When JCR API returns 401/500 (no auth session), this module uses
undetected-chromedriver to complete the Clarivate IP-based auth flow
and caches the resulting session cookies (IC2_SID, PSSID, etc.).
These cookies are then injected into curl_cffi sessions for subsequent requests.
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from ..config.settings import get as cfg

logger = logging.getLogger(__name__)

# Auth cookies we care about
AUTH_COOKIE_NAMES = frozenset({
    "IC2_SID", "PSSID", "ACCESS_METHOD", "userAuthType", "userAuthIDType",
    "CUSTOMER_NAME", "CUSTOMER_GROUP_ID", "IP_SET_ID", "IP_SET_ID_NAME",
    "SUBSCRIPTION_GROUP_ID", "SUBSCRIPTION_GROUP_NAME", "E_GROUP_NAME",
    "ROAMING_DISABLED", "clearStatus",
})

# How long to cache auth cookies before re-auth (seconds)
AUTH_TTL = 3600  # 1 hour

_executor = ThreadPoolExecutor(max_workers=1)


class AuthCookieCache:
    """Manages Clarivate auth cookies obtained via browser IP detection."""

    def __init__(self):
        self._cookies: dict[str, str] = {}
        self._obtained_at: float = 0
        self._lock = asyncio.Lock()
        self._authenticating = False

    @property
    def has_valid_cookies(self) -> bool:
        return bool(self._cookies) and (time.time() - self._obtained_at < AUTH_TTL)

    @property
    def cookies(self) -> dict[str, str]:
        if self.has_valid_cookies:
            return dict(self._cookies)
        return {}

    def invalidate(self):
        """Mark cookies as invalid, forcing re-auth on next request."""
        self._cookies.clear()
        self._obtained_at = 0
        logger.info("Auth cookies invalidated")

    async def ensure_auth(self) -> dict[str, str]:
        """Get valid auth cookies, authenticating via browser if needed."""
        if self.has_valid_cookies:
            return self.cookies

        async with self._lock:
            # Double-check after acquiring lock
            if self.has_valid_cookies:
                return self.cookies

            if self._authenticating:
                logger.warning("Auth already in progress, skipping")
                return {}

            self._authenticating = True
            try:
                logger.info("Starting browser-based Clarivate IP authentication...")
                loop = asyncio.get_event_loop()
                cookies = await loop.run_in_executor(
                    _executor, partial(_browser_auth_sync)
                )
                if cookies:
                    self._cookies = cookies
                    self._obtained_at = time.time()
                    logger.info("Auth cookies obtained: %s",
                                ", ".join(f"{k}={v[:20]}..." for k, v in cookies.items()
                                          if k in ("IC2_SID", "ACCESS_METHOD", "CUSTOMER_NAME")))
                    return dict(self._cookies)
                else:
                    logger.error("Browser auth failed — no auth cookies obtained")
                    return {}
            finally:
                self._authenticating = False


def _browser_auth_sync() -> dict[str, str] | None:
    """Run browser auth flow synchronously (called in thread pool)."""
    try:
        import undetected_chromedriver as uc
    except ImportError:
        logger.error("undetected-chromedriver not installed")
        return None

    options = uc.ChromeOptions()
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1280,720")
    options.add_argument("--lang=zh-TW")

    headless = cfg("browser.headless", True)
    if headless:
        options.add_argument("--headless=new")

    # Detect Chrome version
    import subprocess
    version_main = None
    try:
        result = subprocess.run(
            ["google-chrome", "--version"], capture_output=True, text=True, timeout=5
        )
        version_main = int(result.stdout.strip().split()[-1].split(".")[0])
    except Exception:
        pass

    driver = uc.Chrome(options=options, version_main=version_main)
    driver.set_page_load_timeout(60)

    try:
        # Navigate to JCR — browser will follow the full auth redirect chain
        logger.info("Browser: navigating to jcr.clarivate.com/jcr/home")
        driver.get("https://jcr.clarivate.com/jcr/home")

        # Wait for auth to complete (check for IC2_SID cookie)
        deadline = time.time() + 60  # max 60s
        while time.time() < deadline:
            time.sleep(2)
            cookies = driver.get_cookies()
            cookie_dict = {c["name"]: c["value"] for c in cookies}

            if "IC2_SID" in cookie_dict:
                logger.info("Browser: auth completed, got IC2_SID")
                # Extract auth cookies
                auth_cookies = {}
                for c in cookies:
                    if c["name"] in AUTH_COOKIE_NAMES:
                        # Strip surrounding quotes if present
                        val = c["value"]
                        if val.startswith('"') and val.endswith('"'):
                            val = val[1:-1]
                        auth_cookies[c["name"]] = val
                return auth_cookies

            url = driver.current_url
            title = driver.title
            logger.debug("Browser: waiting for auth... URL=%s title=%s", url[:60], title[:40])

        logger.warning("Browser: auth timed out after 60s")
        try:
            driver.save_screenshot("/tmp/jcr_auth_debug.png")
        except Exception:
            pass
        return None

    except Exception as e:
        logger.error("Browser auth error: %s", e)
        return None

    finally:
        try:
            pid = driver.browser_pid
        except Exception:
            pid = None
        try:
            driver.quit()
        except Exception:
            pass
        # Cleanup
        import os, signal
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
        logger.info("Browser: auth session closed")


# Global instance
auth_cache = AuthCookieCache()
