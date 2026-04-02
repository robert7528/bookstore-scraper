from __future__ import annotations

import re
from typing import Any

from selectolax.parser import HTMLParser

from ..config.loader import load_site_config
from ..models.schemas import BookItem, SearchResult
from ..scraper.engine import ScraperEngine


def _parse_field(node_html: str, rule: dict[str, Any]) -> str:
    tree = HTMLParser(node_html)
    selector = rule.get("selector", "")
    if not selector:
        return rule.get("default", "")

    node = tree.css_first(selector)
    if node is None:
        return rule.get("default", "")

    attr = rule.get("attr")
    value = node.attributes.get(attr) if attr else node.text(strip=True)
    if not value:
        return rule.get("default", "")

    if regex := rule.get("regex"):
        m = re.search(regex, node.html or "")
        value = m.group(1) if m else value

    if concat := rule.get("concat"):
        if not value.startswith("http"):
            value = concat + value

    return value.strip()


async def run_search(site: str, keyword: str, page: int = 1) -> SearchResult:
    cfg = load_site_config(site)
    search_cfg = cfg["search"]
    parse_cfg = cfg["parse"]

    url = search_cfg["url"].format(keyword=keyword, page=page)

    async with ScraperEngine() as engine:
        resp = await engine.get(url)

    if resp.status_code != 200:
        return SearchResult(exception=f"HTTP {resp.status_code}")

    html = resp.text
    tree = HTMLParser(html)

    # Total count
    total = 0
    total_node = tree.css_first(parse_cfg["total"]["selector"])
    if total_node:
        digits = re.sub(r"\D", "", total_node.text(strip=True))
        total = int(digits) if digits else 0

    # Records
    record_selector = parse_cfg["record"]["selector"]
    field_rules = parse_cfg["fields"]
    items: list[BookItem] = []

    for record_node in tree.css(record_selector):
        record_html = record_node.html or ""
        data = {}
        for rule in field_rules:
            data[rule["name"]] = _parse_field(record_html, rule)
        items.append(BookItem(**data))

    return SearchResult(total=total, page=page, items=items)
