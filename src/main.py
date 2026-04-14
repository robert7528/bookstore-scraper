from __future__ import annotations

import logging
import time
import uuid

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from .config.loader import list_sites
from .config.settings import get as cfg
from .models.schemas import ProxyRequest, ProxyResponse, SearchRequest, SearchResult
from .monitor import RequestMetrics, get_current, get_history, record, snapshot
from .rate_limiter import rate_limiter
from .scraper.engine import _is_challenged, _is_challenged_content
from .scraper.session_manager import SessionManager
from .sites.runner import run_search

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Suppress noisy asyncio "Task was destroyed but it is pending" warnings
# from MitM proxy tunnel cleanup — harmless, but floods the log
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

session_mgr = SessionManager()
_browser_pool = None


def _get_browser_pool():
    global _browser_pool
    if _browser_pool is None:
        engine = cfg("browser.engine", "playwright")
        if engine == "undetected":
            try:
                from .scraper.undetected_browser import UndetectedBrowser
                _browser_pool = UndetectedBrowser()
                logger.info("Browser engine: undetected-chromedriver")
            except ImportError:
                logger.warning("undetected-chromedriver not installed, falling back to playwright")
                engine = "playwright"
        if engine == "playwright":
            try:
                from .scraper.browser_pool import BrowserPool
                _browser_pool = BrowserPool()
                logger.info("Browser engine: playwright")
            except ImportError:
                logger.warning("playwright not installed, browser fallback disabled")
    return _browser_pool




_proxy_server = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _proxy_server
    # Start forward proxy if enabled
    if cfg("proxy.enabled", False):
        from .proxy.server import ProxyServer
        proxy_host = cfg("proxy.host", "0.0.0.0")
        proxy_port = cfg("proxy.port", 8102)
        _proxy_server = ProxyServer(proxy_host, proxy_port, session_mgr, _get_browser_pool())
        await _proxy_server.start()

    yield

    # Shutdown
    if _proxy_server:
        await _proxy_server.stop()
    await session_mgr.close_all()
    if _browser_pool:
        await _browser_pool.close()


app = FastAPI(title="Bookstore Scraper", version="0.4.0", lifespan=lifespan)


# --- HyFSE Python Driver endpoint ---

@app.post("/request", response_model=ProxyResponse)
async def proxy_request(req: ProxyRequest):
    """HyFSE Python Driver — 接收完整 HTTP 請求，用 curl_cffi 發送，回傳原始 response。

    - 不帶 session_id：每次新建 session（無狀態）
    - 帶 session_id：復用同一 session，cookie 自動保持
    - 碰到 Cloudflare challenge 自動 fallback 到 Playwright 瀏覽器
    """
    from .scraper.base import Response as ScraperResponse

    t0 = time.perf_counter()
    before = snapshot()
    sid = req.session_id or ""
    own_session = not sid
    driver = "curl"

    # Rate limit per domain
    await rate_limiter.wait(req.url)

    if not sid:
        sid = uuid.uuid4().hex[:12]

    session = session_mgr.get_or_create(sid, impersonate=req.impersonate, timeout=req.timeout)

    try:
        method = req.method.upper()
        if method == "GET":
            r = await session.get(req.url, headers=req.headers or None)
        elif method == "POST":
            r = await session.post(req.url, headers=req.headers or None, data=req.body if req.body else None)
        else:
            return ProxyResponse(status_code=0, exception=f"Unsupported method: {method}")

        # Check for Cloudflare challenge — fallback to browser pool
        curl_resp = ScraperResponse(status_code=r.status_code, text=r.text, headers=dict(r.headers), url=str(r.url))
        if _is_challenged(curl_resp):
            logger.warning("Cloudflare challenge detected for %s, falling back to browser", req.url)
            pool = _get_browser_pool()
            if pool:
                driver = "browser"
                if method == "GET":
                    browser_resp = await pool.get(req.url, headers=req.headers or None)
                else:
                    browser_resp = await pool.post(req.url, headers=req.headers or None, data=req.body if req.body else None)

                if not _is_challenged_content(browser_resp):
                    elapsed = time.perf_counter() - t0
                    after = snapshot()
                    record(RequestMetrics(
                        url=req.url, method=method, driver=driver,
                        status_code=browser_resp.status_code, elapsed=elapsed,
                        before=before, after=after,
                    ))
                    logger.info("%s %s → %d via BrowserPool (%.2fs)", method, req.url, browser_resp.status_code, elapsed)
                    return ProxyResponse(
                        status_code=browser_resp.status_code,
                        headers=browser_resp.headers,
                        body=browser_resp.text,
                        url=browser_resp.url,
                        elapsed=round(elapsed, 3),
                        session_id=sid if req.session_id else "",
                    )
            else:
                logger.warning("Playwright not installed, cannot fallback to browser")

        elapsed = time.perf_counter() - t0
        after = snapshot()
        record(RequestMetrics(
            url=req.url, method=method, driver=driver,
            status_code=r.status_code, elapsed=elapsed,
            before=before, after=after,
        ))
        logger.info("%s %s → %d (%.2fs) [session=%s]", method, req.url, r.status_code, elapsed, sid)
        return ProxyResponse(
            status_code=r.status_code,
            headers=dict(r.headers),
            body=r.text,
            url=str(r.url),
            elapsed=round(elapsed, 3),
            session_id=sid if req.session_id else "",
        )
    except Exception as e:
        elapsed = time.perf_counter() - t0
        after = snapshot()
        record(RequestMetrics(
            url=req.url, method=req.method, driver=driver,
            status_code=0, elapsed=elapsed,
            before=before, after=after,
        ))
        logger.error("%s %s → error: %s (%.2fs)", req.method, req.url, e, elapsed)
        return ProxyResponse(status_code=0, elapsed=round(elapsed, 3), exception=str(e))
    finally:
        if own_session:
            await session_mgr.close(sid)


# --- Fetch proxy endpoint (for HyFSE config-only integration) ---

@app.get("/fetch/{target_url:path}")
async def fetch_proxy(target_url: str, request: Request):
    """透明代理 — HyFSE 設定檔只需改 URL 即可使用，回傳 raw HTML。

    HyFSE 設定範例:
        原本: "target": "https://www.books.com.tw/products/##searchkey##"
        改成: "target": "http://localhost:8101/fetch/https://www.books.com.tw/products/##searchkey##"

    HyFSE 的 driver 維持 curl，parser 不用改。
    """
    from .scraper.base import Response as ScraperResponse

    t0 = time.perf_counter()
    before = snapshot()
    driver = "curl"

    # Extract raw URL from request path to preserve original encoding (e.g. %E4%B8%AD)
    # FastAPI auto-decodes path params, which breaks URL-encoded search terms
    raw_path = request.url.path
    target_url = raw_path.split("/fetch/", 1)[1] if "/fetch/" in raw_path else target_url

    # Reconstruct query string if any
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    # Ensure URL has protocol
    if not target_url.startswith("http"):
        target_url = "https://" + target_url

    # Auto-encode non-ASCII characters (e.g. Chinese keywords from HyFSE)
    # '%' is kept safe to avoid double-encoding already-encoded URLs
    from urllib.parse import quote, urlparse, urlunparse
    parsed = urlparse(target_url)
    encoded_path = quote(parsed.path, safe="/:@!$&'()*+,;=-._~%")
    if parsed.path != encoded_path:
        target_url = urlunparse(parsed._replace(path=encoded_path))

    # Rate limit per domain to avoid WAF blocking
    wait_time = await rate_limiter.wait(target_url)
    if wait_time > 0:
        logger.info("Rate limited %.2fs for %s", wait_time, target_url[:80])

    from urllib.parse import urlparse
    domain = urlparse(target_url).netloc or "unknown"
    sid = f"fetch_{domain}"
    session = session_mgr.get_or_create(sid)

    try:
        # Always try curl first — with session reuse + rate limiter it often works
        r = await session.get(target_url)
        curl_resp = ScraperResponse(status_code=r.status_code, text=r.text, headers=dict(r.headers), url=str(r.url))
        if not _is_challenged(curl_resp):
            elapsed = time.perf_counter() - t0
            after = snapshot()
            record(RequestMetrics(
                url=target_url, method="GET", driver=driver,
                status_code=r.status_code, elapsed=elapsed,
                before=before, after=after,
            ))
            logger.info("FETCH %s → %d via curl (%.2fs)", target_url, r.status_code, elapsed)
            return HTMLResponse(content=r.text, status_code=r.status_code)

        # curl got challenged — try browser fallback
        logger.warning("Fetch proxy: challenge detected for %s, using browser", target_url)
        pool = _get_browser_pool()
        if pool:
            driver = "browser"
            browser_resp = await pool.get(target_url)
            if not _is_challenged_content(browser_resp):
                elapsed = time.perf_counter() - t0
                after = snapshot()
                record(RequestMetrics(
                    url=target_url, method="GET", driver=driver,
                    status_code=browser_resp.status_code, elapsed=elapsed,
                    before=before, after=after,
                ))
                logger.info("FETCH %s → %d via Browser (%.2fs)", target_url, browser_resp.status_code, elapsed)
                return HTMLResponse(content=browser_resp.text, status_code=browser_resp.status_code)

        # All methods failed — return curl result as-is
        elapsed = time.perf_counter() - t0
        after = snapshot()
        record(RequestMetrics(
            url=target_url, method="GET", driver=driver,
            status_code=r.status_code, elapsed=elapsed,
            before=before, after=after,
        ))
        logger.warning("FETCH %s → %d (all methods failed, %.2fs)", target_url, r.status_code, elapsed)
        return HTMLResponse(content=r.text, status_code=r.status_code)
    except Exception as e:
        elapsed = time.perf_counter() - t0
        logger.error("FETCH %s → error: %s (%.2fs)", target_url, e, elapsed)
        # Close session on error so next request gets a fresh one
        await session_mgr.close(sid)
        raise HTTPException(status_code=502, detail=str(e))


# --- Monitor endpoints ---

@app.get("/monitor")
async def monitor_current():
    """目前的 CPU / Memory 使用狀態。"""
    return get_current()


@app.get("/monitor/history")
async def monitor_history(limit: int = 20):
    """最近的請求資源使用紀錄。"""
    return {"records": get_history(limit)}


# --- Session endpoints ---

@app.delete("/session/{session_id}")
async def close_session(session_id: str):
    """手動關閉 session。"""
    closed = await session_mgr.close(session_id)
    return {"closed": closed}


@app.get("/sessions")
async def list_sessions():
    """列出所有活躍 session。"""
    return {"sessions": session_mgr.list_sessions()}


# --- Existing endpoints ---

@app.get("/")
async def root():
    return {"service": "bookstore-scraper", "version": "0.4.0"}


@app.get("/sites")
async def get_sites():
    return {"sites": list_sites()}


@app.post("/search", response_model=SearchResult)
async def search(req: SearchRequest):
    try:
        return await run_search(site=req.site, keyword=req.keyword, page=req.page)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
