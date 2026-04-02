from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SETTINGS_PATH = Path(__file__).resolve().parent.parent.parent / "configs" / "settings.yaml"

_cache: dict[str, Any] | None = None


def load_settings() -> dict[str, Any]:
    global _cache
    if _cache is None:
        if SETTINGS_PATH.exists():
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                _cache = yaml.safe_load(f) or {}
        else:
            _cache = {}
    return _cache


def get(key: str, default: Any = None) -> Any:
    """Dot-notation access: get('server.port', 8000)"""
    settings = load_settings()
    parts = key.split(".")
    current = settings
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current
