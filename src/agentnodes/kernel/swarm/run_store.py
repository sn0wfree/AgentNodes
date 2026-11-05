"""In-memory run result store for swarm execution (LRU eviction)."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any


class RunStore:
    """内存中的 swarm 运行结果存储。LRU 淘汰，保留最近 MAX_RUNS 个。"""

    MAX_RUNS = 20

    def __init__(self, max_runs: int = MAX_RUNS) -> None:
        self._max_runs = max_runs
        self._runs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def save(self, run_id: str, result: dict[str, Any]) -> None:
        """保存运行结果。超过容量时淘汰最旧的。"""
        with self._lock:
            self._runs[run_id] = result
            self._runs.move_to_end(run_id)
            while len(self._runs) > self._max_runs:
                self._runs.popitem(last=False)

    def get(self, run_id: str) -> dict[str, Any] | None:
        """获取运行结果。不存在返回 None。"""
        with self._lock:
            return self._runs.get(run_id)

    def list_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """列出最近的运行结果。"""
        with self._lock:
            items = list(self._runs.values())[-limit:]
            return items

    def __len__(self) -> int:
        with self._lock:
            return len(self._runs)

    def __contains__(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._runs
