from __future__ import annotations

import logging
import time

from curl_cffi.requests import AsyncSession

from ..config.settings import get as cfg
from .curl_scraper import DEFAULT_IMPERSONATE

logger = logging.getLogger(__name__)

SESSION_TTL = cfg("session.ttl", 300)


class SessionManager:
    """Manage named curl_cffi sessions for multi-step flows.

    HyFSE sends a session_id to reuse the same session (with cookies)
    across multiple requests. Sessions auto-expire after TTL.
    """

    def __init__(self):
        self._sessions: dict[str, tuple[AsyncSession, float]] = {}

    def get_or_create(self, session_id: str, *, impersonate: str = DEFAULT_IMPERSONATE, timeout: int = 30) -> AsyncSession:
        self._cleanup_expired()

        if session_id in self._sessions:
            session, _ = self._sessions[session_id]
            self._sessions[session_id] = (session, time.time())
            logger.debug("Reusing session: %s", session_id)
            return session

        session = AsyncSession(impersonate=impersonate, timeout=timeout)
        self._sessions[session_id] = (session, time.time())
        logger.info("Created session: %s (total: %d)", session_id, len(self._sessions))
        return session

    async def close(self, session_id: str) -> bool:
        if session_id in self._sessions:
            session, _ = self._sessions.pop(session_id)
            await session.close()
            logger.info("Closed session: %s", session_id)
            return True
        return False

    async def close_all(self) -> None:
        for sid in list(self._sessions):
            await self.close(sid)

    def list_sessions(self) -> list[dict]:
        now = time.time()
        return [
            {"session_id": sid, "age": round(now - ts, 1)}
            for sid, (_, ts) in self._sessions.items()
        ]

    def _cleanup_expired(self) -> None:
        import asyncio
        now = time.time()
        expired = [sid for sid, (_, ts) in self._sessions.items() if now - ts > SESSION_TTL]
        for sid in expired:
            session, _ = self._sessions.pop(sid)
            try:
                asyncio.get_event_loop().create_task(session.close())
            except RuntimeError:
                pass
            logger.info("Expired session: %s", sid)
