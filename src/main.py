from __future__ import annotations

import logging
import time
import uuid

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .config.loader import list_sites
from .models.schemas import ProxyRequest, ProxyResponse, SearchRequest, SearchResult
from .scraper.session_manager import SessionManager
from .sites.runner import run_search

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

session_mgr = SessionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await session_mgr.close_all()


app = FastAPI(title="Bookstore Scraper", version="0.3.0", lifespan=lifespan)


# --- HyFSE Python Driver endpoint ---

@app.post("/request", response_model=ProxyResponse)
async def proxy_request(req: ProxyRequest):
    """HyFSE Python Driver — 接收完整 HTTP 請求，用 curl_cffi 發送，回傳原始 response。

    - 不帶 session_id：每次新建 session（無狀態）
    - 帶 session_id：復用同一 session，cookie 自動保持
    """
    t0 = time.perf_counter()
    sid = req.session_id or ""
    own_session = not sid  # 沒帶 session_id 的話，用完即關

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
        logger.error("%s %s → error: %s (%.2fs)", method, req.url, e, elapsed)
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
