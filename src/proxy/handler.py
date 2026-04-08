"""Forward proxy request handler — execute requests via curl_cffi."""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from ..config.settings import get as cfg
from ..rate_limiter import rate_limiter
from ..scraper.engine import _is_challenged_content, CHALLENGE_SIGNS
from ..scraper.session_manager import SessionManager

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


async def handle_proxy_request(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    session_mgr: SessionManager,
    browser_pool=None,
) -> tuple[int, dict[str, str], bytes]:
    """Execute proxied request via curl_cffi and return (status, headers, body_bytes).

    Flow:
    1. Get/create session per domain (reuses cookies)
    2. Rate limit per domain
    3. Execute via curl_cffi AsyncSession
    4. If HTML + challenged → browser fallback
    5. Return complete response
    """
    parsed = urlparse(url)
    domain = parsed.netloc or "unknown"
    sid = f"proxy_{domain}"

    session = session_mgr.get_or_create(sid)

    # Clear session cookie jar — proxy should NOT manage cookies.
    # HyProxy/browser manages cookies and sends them via request headers.
    # Dual cookie management (curl_cffi session + HyProxy) causes auth loops.
    session.cookies.clear()

    # Rate limit
    wait_time = await rate_limiter.wait(url)
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

    try:
        # Proxy must NOT follow redirects — return 3xx as-is to the client
        # so HyProxy/browser handles the redirect through the correct tunnel
        kwargs = {"headers": clean_headers or None, "allow_redirects": False}

        if method.upper() == "GET":
            r = await session.get(url, **kwargs)
        elif method.upper() == "POST":
            r = await session.post(url, data=body or None, **kwargs)
        elif method.upper() == "HEAD":
            r = await session.head(url, **kwargs)
        elif method.upper() == "PUT":
            r = await session.put(url, data=body or None, **kwargs)
        elif method.upper() == "DELETE":
            r = await session.delete(url, **kwargs)
        else:
            r = await session.get(url, **kwargs)

        status_code = r.status_code
        resp_headers = dict(r.headers)
        resp_body = r.content

        # Challenge detection — only for text/html responses
        content_type = resp_headers.get("content-type", "")
        if "text/html" in content_type:
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
                    resp_headers = {"content-type": "text/html; charset=utf-8"}
                    resp_body = browser_resp.text.encode("utf-8")
                    logger.info("Proxy: %s → %d via browser", url[:80], status_code)

        # Remove hop-by-hop and stale encoding headers from response
        resp_headers = {
            k: v for k, v in resp_headers.items()
            if k.lower() not in HOP_BY_HOP and k.lower() not in STRIP_RESPONSE_HEADERS
        }

        logger.info("PROXY %s %s → %d (%d bytes)", method, url[:80], status_code, len(resp_body))
        return status_code, resp_headers, resp_body

    except Exception as e:
        logger.error("PROXY %s %s → error: %s", method, url[:80], e)
        error_body = f"502 Bad Gateway: {e}".encode("utf-8")
        return 502, {"content-type": "text/plain"}, error_body


def build_http_response(status_code: int, headers: dict[str, str], body: bytes) -> bytes:
    """Build raw HTTP/1.1 response bytes."""
    reason = _status_reason(status_code)
    lines = [f"HTTP/1.1 {status_code} {reason}"]

    # Set content-length
    headers["content-length"] = str(len(body))
    headers.pop("transfer-encoding", None)

    for key, value in headers.items():
        lines.append(f"{key}: {value}")

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
