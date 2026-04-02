from __future__ import annotations

import abc
from dataclasses import dataclass, field


@dataclass
class Response:
    status_code: int
    text: str
    headers: dict[str, str] = field(default_factory=dict)
    url: str = ""


class BaseScraper(abc.ABC):
    """Unified scraper interface — swap implementations without touching business logic."""

    @abc.abstractmethod
    async def get(self, url: str, *, headers: dict | None = None, params: dict | None = None) -> Response: ...

    @abc.abstractmethod
    async def post(self, url: str, *, headers: dict | None = None, data: dict | None = None, json: dict | None = None) -> Response: ...

    @abc.abstractmethod
    async def close(self) -> None: ...

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
