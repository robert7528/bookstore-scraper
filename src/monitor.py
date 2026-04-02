"""System resource monitor — tracks CPU and memory usage per request."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

try:
    import psutil
    _process = psutil.Process(os.getpid())
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


@dataclass
class ResourceSnapshot:
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    memory_percent: float = 0.0
    open_fds: int = 0
    threads: int = 0
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return {
            "cpu_percent": self.cpu_percent,
            "memory_mb": round(self.memory_mb, 1),
            "memory_percent": round(self.memory_percent, 1),
            "open_fds": self.open_fds,
            "threads": self.threads,
        }


@dataclass
class RequestMetrics:
    url: str = ""
    method: str = ""
    driver: str = ""  # curl / browser
    status_code: int = 0
    elapsed: float = 0.0
    before: ResourceSnapshot = field(default_factory=ResourceSnapshot)
    after: ResourceSnapshot = field(default_factory=ResourceSnapshot)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "method": self.method,
            "driver": self.driver,
            "status_code": self.status_code,
            "elapsed": round(self.elapsed, 3),
            "resources": {
                "before": self.before.to_dict(),
                "after": self.after.to_dict(),
                "delta_memory_mb": round(self.after.memory_mb - self.before.memory_mb, 1),
            },
        }


# Recent metrics history
_history: list[dict] = []
MAX_HISTORY = 100


def snapshot() -> ResourceSnapshot:
    if not HAS_PSUTIL:
        return ResourceSnapshot(timestamp=time.time())

    mem = _process.memory_info()
    try:
        fds = _process.num_fds()
    except AttributeError:
        # Windows doesn't have num_fds
        fds = _process.num_handles()

    return ResourceSnapshot(
        cpu_percent=_process.cpu_percent(interval=None),
        memory_mb=mem.rss / 1024 / 1024,
        memory_percent=_process.memory_percent(),
        open_fds=fds,
        threads=_process.num_threads(),
        timestamp=time.time(),
    )


def record(metrics: RequestMetrics):
    entry = metrics.to_dict()
    _history.append(entry)
    if len(_history) > MAX_HISTORY:
        _history.pop(0)

    logger.info(
        "Resource: %s %s via %s → %d | mem=%.1fMB (delta=%+.1fMB) cpu=%.1f%% threads=%d",
        metrics.method, metrics.url[:60], metrics.driver, metrics.status_code,
        metrics.after.memory_mb,
        metrics.after.memory_mb - metrics.before.memory_mb,
        metrics.after.cpu_percent,
        metrics.after.threads,
    )


def get_history(limit: int = 20) -> list[dict]:
    return _history[-limit:]


def get_current() -> dict:
    s = snapshot()
    return {
        "psutil_available": HAS_PSUTIL,
        **s.to_dict(),
        "history_count": len(_history),
    }
