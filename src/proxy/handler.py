"""Forward proxy request handler — execute requests via curl_cffi."""
from __future__ import annotations

import fnmatch
import logging
import re
from urllib.parse import urlparse

from ..config.settings import get as cfg
from ..rate_limiter import DomainRateLimiter
from ..scraper.engine import _is_challenged_content, CHALLENGE_SIGNS
from ..scraper.session_manager import SessionManager

# Proxy has its own rate limiter (default 0 = no limit)
_proxy_rate_interval = cfg("proxy.rate_limit_interval", 0)
proxy_rate_limiter = DomainRateLimiter(_proxy_rate_interval)

# JCR browser fetch mode — for NAT pool environments where outgoing IP is unstable
_browser_fetch_enabled = cfg("proxy.browser_fetch", False)

logger = logging.getLogger(__name__)

# In-memory cache for content-hashed static assets (Angular bundles etc.).
# URL has content hash → URL change = content change, safe to cache forever
# until service restart. Drops first-user 12s pain for subsequent users.
_asset_cache: dict[str, tuple[int, list[tuple[str, str]], bytes]] = {}
_asset_cache_max_bytes = 100 * 1024 * 1024  # 100MB hard cap
_asset_cache_current_bytes = 0


# Match "{name}.{16+ hex}.{js|css}" anywhere in path — Angular/webpack
# content-hashed filename convention. Path prefix varies across apps
# (e.g. /static/, /public/, /jcr/static/).
_HASHED_ASSET_RE = re.compile(r"/[^/]+\.[0-9a-f]{16,}\.(js|css)(?:$|[?#])")


def _is_hashed_asset(url: str, content_type: str) -> bool:
    """Cacheable if JS/CSS with content-hash filename (Angular/webpack pattern)."""
    if "javascript" not in content_type and "css" not in content_type:
        return False
    return bool(_HASHED_ASSET_RE.search(url))


# Domains to block at proxy layer (return 204 immediately, no network call).
# Covers analytics/telemetry endpoints that hang or timeout and pollute logs.
_block_domain_patterns: list[str] = []

def _load_block_domains():
    global _block_domain_patterns
    _block_domain_patterns = [
        p.lower() for p in cfg("proxy.block_domains", [
            "snowplow-collector.staging.userintel.dev.sp.aws.clarivate.net",
            ".snowplow-collector.",
        ])
    ]

_load_block_domains()


def _is_blocked_domain(domain: str) -> bool:
    """Match settings.yaml proxy.block_domains patterns against domain.

    Uses shell-style glob patterns (fnmatch):
      "foo.com"      = exact match
      "*.foo.com"    = match any *.foo.com subdomain
      "foo-*"        = match anything starting with "foo-"
      "*pendo*"      = match anything containing "pendo"
    """
    d = domain.lower()
    for p in _block_domain_patterns:
        if fnmatch.fnmatch(d, p):
            return True
    return False


# Headers that should not be forwarded between proxy hops
HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade",
})

# Headers to strip from response
STRIP_RESPONSE_HEADERS = frozenset({
    "content-encoding",                  # body already decompressed by curl_cffi
    "content-length",                    # will be recalculated from actual body size
    "content-security-policy",           # CSP blocks resources when accessed via proxy domain
    "content-security-policy-report-only",
    "strict-transport-security",         # HSTS for original domain, not applicable via proxy
    "x-frame-options",                   # may block embedding through proxy
})


# Cookies managed by HyPass — kept in curl_cffi session, not forwarded to HyProxy.
# Loaded from settings.yaml; defaults cover CF + Clarivate auth cookies.
_managed_cookie_names: set[str] = set()

def _load_managed_cookies():
    global _managed_cookie_names
    names = cfg("proxy.managed_cookies", ["__cf_bm", "cf_clearance", "PSSID", "IC2_SID"])
    _managed_cookie_names = {n.lower() for n in names}

_load_managed_cookies()


def _is_managed_cookie(set_cookie_value: str) -> bool:
    """Check if a Set-Cookie header is a HyPass-managed cookie."""
    name = set_cookie_value.split("=", 1)[0].strip().lower()
    return name in _managed_cookie_names


async def _curl_request(session_mgr: SessionManager, sid: str, method: str, url: str, headers: dict, body: bytes):
    """Execute curl request, retry once with fresh session if session was closed."""
    for attempt in range(2):
        session = session_mgr.get_or_create(sid)
        # Clear non-managed cookies — HyProxy/browser manages those via headers.
        # Keep managed cookies (CF + Clarivate auth) in session so curl_cffi
        # handles them directly, preventing HyProxy cookie-domain rewrite
        # from merging different sites' cookies and causing conflicts.
        for name in list(session.cookies.keys()):
            if name.lower() not in _managed_cookie_names:
                session.cookies.delete(name)

        kwargs = {"headers": headers or None, "allow_redirects": False}
        try:
            m = method.upper()
            if m == "GET":
                return await session.get(url, **kwargs)
            elif m == "POST":
                return await session.post(url, data=body or None, **kwargs)
            elif m == "HEAD":
                return await session.head(url, **kwargs)
            elif m == "PUT":
                return await session.put(url, data=body or None, **kwargs)
            elif m == "DELETE":
                return await session.delete(url, **kwargs)
            else:
                return await session.get(url, **kwargs)
        except Exception as e:
            if attempt == 0 and "closed" in str(e).lower():
                logger.warning("Session closed for %s, recreating", sid)
                session_mgr.remove(sid)
                continue
            raise


def _needs_browser_fetch(domain: str, url: str) -> bool:
    """Check if this request should go through browser fetch (NAT pool workaround)."""
    if not _browser_fetch_enabled:
        return False
    return domain == "jcr.clarivate.com" and "/api/" in url


async def handle_proxy_request(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    session_mgr: SessionManager,
    browser_pool=None,
) -> tuple[int, list[tuple[str, str]], bytes]:
    """Execute proxied request via curl_cffi and return (status, headers, body_bytes).

    Flow:
    1. Get/create session per domain (reuses cookies)
    2. Rate limit per domain
    3. Execute via curl_cffi AsyncSession
    4. If HTML + challenged → browser fallback
    5. Return complete response

    If proxy.browser_fetch=true, JCR API requests are routed through a
    persistent browser session to maintain IP consistency (NAT pool workaround).
    """
    parsed = urlparse(url)
    domain = parsed.netloc or "unknown"
    sid = f"proxy_{domain}"

    # Block useless telemetry/analytics endpoints that hang and pollute logs.
    # Return 204 No Content immediately — no network call, no 30s timeout.
    if _is_blocked_domain(domain):
        logger.info("PROXY %s %s → 204 (blocked domain)", method, url[:80])
        return 204, [], b""

    # Cached content-hashed static asset hit — skip network + patching.
    if method.upper() == "GET" and url in _asset_cache:
        cached_status, cached_headers, cached_body = _asset_cache[url]
        logger.info("PROXY GET %s → %d (%d bytes) cache hit", url[:80], cached_status, len(cached_body))
        return cached_status, cached_headers, cached_body

    # Rate limit (proxy uses its own interval, default 0 = no limit)
    wait_time = await proxy_rate_limiter.wait(url)
    if wait_time > 0:
        logger.info("Proxy rate limited %.2fs for %s", wait_time, url[:80])

    # Clean hop-by-hop headers
    clean_headers = {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}
    # Remove host header (curl_cffi sets it from URL)
    clean_headers.pop("host", None)
    clean_headers.pop("Host", None)
    # Remove accept-encoding — let curl_cffi handle its own encoding negotiation
    clean_headers.pop("accept-encoding", None)
    clean_headers.pop("Accept-Encoding", None)

    # JCR API via browser fetch (NAT pool workaround, off by default)
    if _needs_browser_fetch(domain, url):
        from .jcr_browser import jcr_browser
        logger.info("JCR API %s %s → browser fetch", method, url[:80])
        try:
            status, resp_headers, resp_body = await jcr_browser.fetch(
                method, url, clean_headers, body
            )
            resp_header_list = [(k, v) for k, v in resp_headers.items()]
            resp_header_list = [
                (k, v) for k, v in resp_header_list
                if k.lower() not in STRIP_RESPONSE_HEADERS
            ]
            logger.info("PROXY %s %s → %d (%d bytes) via browser",
                        method, url[:80], status, len(resp_body))
            return status, resp_header_list, resp_body
        except Exception as e:
            logger.error("JCR browser fetch failed: %s, falling back to curl", e)

    try:
        r = await _curl_request(session_mgr, sid, method, url, clean_headers, body)

        status_code = r.status_code
        # Use multi_items() to preserve duplicate headers (e.g. multiple Set-Cookie)
        resp_header_list = list(r.headers.multi_items())
        resp_body = r.content
        content_type = r.headers.get("content-type", "")

        # Challenge detection — only for text/html, skip 3xx redirects
        is_redirect = 300 <= status_code < 400
        if "text/html" in content_type and not is_redirect:
            text = r.text[:5000]
            is_challenged = (
                "<title>Just a moment...</title>" in text
                or (status_code != 200 and len(r.text) < 1000)
                or any(sign in text for sign in CHALLENGE_SIGNS) and len(r.text) < 15000
            )

            if is_challenged and browser_pool:
                logger.warning("Proxy: challenge detected for %s, using browser", url[:80])
                from ..scraper.base import Response as ScraperResponse
                browser_resp = await browser_pool.get(url)
                if not _is_challenged_content(browser_resp):
                    status_code = browser_resp.status_code
                    resp_header_list = [("content-type", "text/html; charset=utf-8")]
                    resp_body = browser_resp.text.encode("utf-8")
                    logger.info("Proxy: %s → %d via browser", url[:80], status_code)

        # Fix Clarivate Angular app domain detection for HyProxy by-domain mode.
        # The Angular app checks l_access=["access.","access-"] to detect if it's
        # running on an access.clarivate.com subdomain. In HyProxy by-domain mode,
        # the hostname is "access-clarivate-com.xxx.edu.tw" which contains "access-"
        # and triggers detectSession logic that fails → login loop.
        # Fix: change the check to only match exact Clarivate domains.
        if (domain == "access.clarivate.com"
                and "javascript" in content_type
                and b'l_access=["access.","access-"]' in resp_body):
            resp_body = resp_body.replace(
                b'l_access=["access.","access-"]',
                b'l_access=["access.clarivate.com","__noop__"]'
            )
            logger.info("Patched access.clarivate.com Angular domain detection for %s", url[:80])

        # Remove hop-by-hop, stale encoding headers, and managed cookies from response.
        # Managed cookies (CF + Clarivate auth) are kept in curl_cffi session
        # and must NOT be forwarded to HyProxy — its cookie-domain rewrite
        # merges them across sites, causing cookie conflicts.
        filtered = []
        for k, v in resp_header_list:
            if k.lower() in HOP_BY_HOP or k.lower() in STRIP_RESPONSE_HEADERS:
                continue
            if k.lower() == "set-cookie" and _is_managed_cookie(v):
                cookie_name = v.split("=", 1)[0].strip()
                logger.info("Managed cookie filtered: %s from %s", cookie_name, url[:80])
                continue
            filtered.append((k, v))
        resp_header_list = filtered

        # Cache content-hashed static assets for next request.
        # Content-hashed URL → safe to cache until service restart.
        if (method.upper() == "GET"
                and status_code == 200
                and _is_hashed_asset(url, content_type)
                and url not in _asset_cache):
            global _asset_cache_current_bytes
            size = len(resp_body)
            if _asset_cache_current_bytes + size <= _asset_cache_max_bytes:
                _asset_cache[url] = (status_code, resp_header_list, resp_body)
                _asset_cache_current_bytes += size
                logger.info("Asset cached: %s (%d bytes, total %d MB)",
                            url[:80], size, _asset_cache_current_bytes // (1024*1024))

        logger.info("PROXY %s %s → %d (%d bytes)", method, url[:80], status_code, len(resp_body))
        return status_code, resp_header_list, resp_body

    except Exception as e:
        logger.error("PROXY %s %s → error: %s", method, url[:80], e)
        error_body = f"502 Bad Gateway: {e}".encode("utf-8")
        return 502, [("content-type", "text/plain")], error_body


def build_http_response(status_code: int, headers: list[tuple[str, str]], body: bytes) -> bytes:
    """Build raw HTTP/1.1 response bytes.

    headers is a list of (name, value) tuples to preserve duplicates (e.g. Set-Cookie).
    """
    reason = _status_reason(status_code)
    lines = [f"HTTP/1.1 {status_code} {reason}"]

    # Filter out transfer-encoding, add content-length
    for key, value in headers:
        if key.lower() != "transfer-encoding":
            lines.append(f"{key}: {value}")
    lines.append(f"content-length: {len(body)}")

    header_block = "\r\n".join(lines) + "\r\n\r\n"
    return header_block.encode("utf-8") + body


def _status_reason(code: int) -> str:
    reasons = {
        200: "OK", 201: "Created", 204: "No Content",
        301: "Moved Permanently", 302: "Found", 304: "Not Modified",
        400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
        404: "Not Found", 405: "Method Not Allowed",
        500: "Internal Server Error", 502: "Bad Gateway",
        503: "Service Unavailable",
    }
    return reasons.get(code, "Unknown")
