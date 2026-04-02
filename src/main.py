from __future__ import annotations

import logging
import time
import uuid

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .config.loader import list_sites
from .models.schemas import ProxyRequest, ProxyResponse, SearchRequest, SearchResult
from .scraper.engine import _is_challenged
from .scraper.session_manager import SessionManager
from .sites.runner import run_search

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

session_mgr = SessionManager()
_browser_pool = None


def _get_browser_pool():
    global _browser_pool
    if _browser_pool is None:
        try:
            from .scraper.browser_pool import BrowserPool
            _browser_pool = BrowserPool()
        except ImportError:
            pass
    return _browser_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await session_mgr.close_all()
    if _browser_pool:
        await _browser_pool.close()


app = FastAPI(title="Bookstore Scraper", version="0.3.0", lifespan=lifespan)


# --- HyFSE Python Driver endpoint ---

@app.post("/request", response_model=ProxyResponse)
async def proxy_request(req: ProxyRequest):
    """HyFSE Python Driver — 接收完整 HTTP 請求，用 curl_cffi 發送，回傳原始 response。

    - 不帶 session_id：每次新建 session（無狀態）
    - 帶 session_id：復用同一 session，cookie 自動保持
    - 碰到 Cloudflare challenge 自動 fallback 到 Playwright 瀏覽器
    """
    from .scraper.base import Response as ScraperResponse  # noqa: E402

    t0 = time.perf_counter()
    sid = req.session_id or ""
    own_session = not sid

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
                if method == "GET":
                    browser_resp = await pool.get(req.url, headers=req.headers or None)
                else:
                    browser_resp = await pool.post(req.url, headers=req.headers or None, data=req.body if req.body else None)

                if not _is_challenged(browser_resp):
                    elapsed = time.perf_counter() - t0
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
        logger.error("%s %s → error: %s (%.2fs)", req.method, req.url, e, elapsed)
        return ProxyResponse(status_code=0, elapsed=round(elapsed, 3), exception=str(e))
    finally:
        if own_session:
            await session_mgr.close(sid)


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
    return {"service": "bookstore-scraper", "version": "0.3.0"}


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
