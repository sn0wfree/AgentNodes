"""Exponential backoff rate limiter for web requests."""

from __future__ import annotations

import threading
import time


class ExponentialBackoff:
    """指数冷却限速器。

    初始冷却 base 秒，每次调用 wait() 翻倍，最大 max_delay 秒。
    调用 reset() 重置回初始值。线程安全。
    """

    def __init__(
        self,
        base: float = 1.0,
        max_delay: float = 30.0,
        factor: float = 2.0,
    ) -> None:
        self._base = base
        self._max_delay = max_delay
        self._factor = factor
        self._current_delay = base
        self._last_call = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        """等待适当时间后才允许下一次请求。"""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._current_delay:
                time.sleep(self._current_delay - elapsed)
            self._last_call = time.monotonic()
            # 指数增长
            self._current_delay = min(
                self._current_delay * self._factor,
                self._max_delay,
            )

    def reset(self) -> None:
        """成功后重置冷却时间。"""
        with self._lock:
            self._current_delay = self._base

    @property
    def current_delay(self) -> float:
        return self._current_delay
