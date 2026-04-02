from __future__ import annotations

import re
from typing import Any

from selectolax.parser import HTMLParser


def parse_html(html: str, rules: list[dict[str, Any]]) -> dict[str, str]:
    """Parse HTML using selectolax with config-driven rules."""
    tree = HTMLParser(html)
    result: dict[str, str] = {}
    for rule in rules:
        name = rule["name"]
        selector = rule.get("selector", "")
        attr = rule.get("attr")
        regex = rule.get("regex")

        node = tree.css_first(selector) if selector else None
        if node is None:
            result[name] = rule.get("default", "")
            continue

        value = node.attributes.get(attr) if attr else node.text(strip=True)
        if value and regex:
            m = re.search(regex, value)
            value = m.group(1) if m else value
        result[name] = (value or "").strip()
    return result


def parse_html_list(html: str, record_selector: str, rules: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Parse a list of items from HTML."""
    tree = HTMLParser(html)
    items = []
    for node in tree.css(record_selector):
        item_html = node.html or ""
        item = parse_html(item_html, rules)
        items.append(item)
    return items
