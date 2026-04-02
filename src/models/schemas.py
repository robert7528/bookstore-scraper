from __future__ import annotations

from pydantic import BaseModel


class SearchRequest(BaseModel):
    site: str
    keyword: str
    page: int = 1
    fields: dict[str, str] = {}


class BookItem(BaseModel):
    title: str = ""
    author: str = ""
    publisher: str = ""
    date: str = ""
    isbn: str = ""
    price: str = ""
    url: str = ""
    photo: str = ""


class SearchResult(BaseModel):
    total: int = 0
    page: int = 1
    items: list[BookItem] = []
    exception: str = ""


# --- HyFSE Python Driver models ---

class ProxyRequest(BaseModel):
    url: str
    method: str = "GET"
    headers: dict[str, str] = {}
    body: str = ""
    timeout: int = 30
    impersonate: str = "chrome136"
    session_id: str = ""  # 空=每次新建，有值=復用同一 session（保持 cookie）


class ProxyResponse(BaseModel):
    status_code: int
    headers: dict[str, str] = {}
    body: str = ""
    url: str = ""
    elapsed: float = 0.0
    exception: str = ""
    session_id: str = ""  # 回傳使用的 session_id
