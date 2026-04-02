from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs" / "sites"


def load_site_config(site: str) -> dict[str, Any]:
    path = CONFIGS_DIR / f"{site}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Site config not found: {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def list_sites() -> list[str]:
    return [p.stem for p in CONFIGS_DIR.glob("*.yaml")]
