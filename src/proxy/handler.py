"""Forward proxy request handler — execute requests via curl_cffi."""
from __future__ import annotations

import logging
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


def _is_cf_cookie(set_cookie_value: str) -> bool:
    """Check if a Set-Cookie header is a Cloudflare cookie."""
    name = set_cookie_value.split("=", 1)[0].strip().lower()
    return name.startswith("__cf") or name == "cf_clearance"


async def _curl_request(session_mgr: SessionManager, sid: str, method: str, url: str, headers: dict, body: bytes):
    """Execute curl request, retry once with fresh session if session was closed."""
    for attempt in range(2):
        session = session_mgr.get_or_create(sid)
        # Clear non-CF cookies — HyProxy/browser manages those via headers.
        # Keep CF cookies (__cf_bm, cf_clearance) in session so curl_cffi
        # handles them directly, preventing HyProxy cookie-domain rewrite
        # from merging different sites' CF tokens and causing conflicts.
        for name in list(session.cookies.keys()):
            if not name.startswith("__cf") and not name.startswith("cf_clearance"):
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

        # Remove hop-by-hop, stale encoding headers, and CF cookies from response.
        # CF cookies (__cf_bm, cf_clearance) are managed by curl_cffi session
        # and must NOT be forwarded to HyProxy — its cookie-domain rewrite
        # merges them across sites, causing Cloudflare token conflicts.
        filtered = []
        for k, v in resp_header_list:
            if k.lower() in HOP_BY_HOP or k.lower() in STRIP_RESPONSE_HEADERS:
                continue
            if k.lower() == "set-cookie" and _is_cf_cookie(v):
                cookie_name = v.split("=", 1)[0].strip()
                logger.info("CF cookie filtered: %s from %s", cookie_name, url[:80])
                continue
            filtered.append((k, v))
        resp_header_list = filtered

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
