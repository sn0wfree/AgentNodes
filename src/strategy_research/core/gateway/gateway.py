"""Gateway 组装（阶段 3b）—— 单进程内核的可运行形态。

阶段 3b 升级：
- engine_factory 从 monkey-patch 升级为构造参数
- 集成 PlatformStore（事件持久化 + blob + artifact 登记）
- 灰度 flag 默认开（旧路径保留但 deprecated）
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from strategy_research.core.gateway.fencing import SessionFence
from strategy_research.core.gateway.session_controller import RunResult, SessionController
from strategy_research.core.orchestration import EntryPoint

logger = logging.getLogger(__name__)


class Gateway:
    """单进程内核：入口 + 控制器 + fence + 事件存储 + 不变量自检。"""

    def __init__(
        self,
        *,
        client: Any,
        system: str | None = None,
        sink: Callable[[str, dict], None] | None = None,
        engine_factory: Callable[[], Any] | None = None,
        data_dir: str | Path | None = None,
        registry: Any | None = None,
        stream: bool = False,
        context_hub: Any | None = None,
        context_policy: Any | None = None,
    ) -> None:
        self._events: list[tuple[str, dict]] = []
        self._external_sink = sink
        combined = self._combined_sink
        self.entry = EntryPoint(sink=combined)
        self.fence = SessionFence()
        self.controller = SessionController(
            self.entry, client=client, system=system, sink=combined,
            engine_factory=engine_factory,
            # R1：写栅栏注入——控制器 settle 前过 fence（INV-4），
            # 认领即超越（INV-5）。同一实例供 verify_invariants 检查。
            fence=self.fence,
            # P5-1b：有 registry 时控制器默认图用 react 节点（无工具调用时
            # 退化为单次 LLM），否则纯 llm 节点。
            registry=registry, stream=stream,
            # B5 R-1（选项 A）：组装根构造 ContextHub 并注入。None = 无上下
            # 文注入——直构 controller 的测试路径行为不变。
            context_hub=context_hub,
            context_policy=context_policy,
        )
        # 阶段 3b：PlatformStore（事件持久化 + blob + artifact 登记）
        self._store = None
        if data_dir is not None:
            from strategy_research.core.storage.platform_store import PlatformStore

            self._store = PlatformStore(data_dir=data_dir)
        # 保持兼容：_build_engine 仍可访问（A1 preset 注册表会替换）

    @property
    def store(self):
        """PlatformStore（可能为 None——data_dir 未配置时纯内存模式）。"""
        return self._store

    def _combined_sink(self, kind: str, data: dict) -> None:
        self._default_sink(kind, data)
        if self._external_sink is not None:
            self._external_sink(kind, data)

    def install_sink(self, sink: Callable[[str, dict], None]) -> None:
        """追加一个外部 sink（P5-1b：用于把引擎事件转发到 SSE）。

        与构造时的 ``sink`` 并存（不替换内部缓冲）。转发异常只记日志，
        不能影响引擎主路径。
        """
        prev = self._external_sink

        def chained(kind: str, data: dict) -> None:
            if prev is not None:
                try:
                    prev(kind, data)
                except Exception:  # noqa: BLE001
                    logger.debug("previous sink failed: %s", kind, exc_info=True)
            try:
                sink(kind, data)
            except Exception:  # noqa: BLE001
                logger.debug("installed sink failed: %s", kind, exc_info=True)

        self._external_sink = chained

    def _default_sink(self, kind: str, data: dict) -> None:
        self._events.append((kind, data))
        # 阶段 3b：事件持久化到 PlatformStore（如果配置了）
        if self._store is not None:
            session_id = data.get("session_id", "default")
            try:
                self._store.emit(session_id, kind, data)
            except Exception:  # noqa: BLE001
                logger.debug("event persistence failed: %s", kind, exc_info=True)
        logger.debug("event %s %s", kind, data)

    # ── 驱动 ─────────────────────────────────────────────

    def run_once(self, kinds: Any = None) -> list[RunResult]:
        """处理当前排队的工单（同步）。A2 换成常驻消费者。

        kinds：可选 WorkKind 集合——chat 只领 CHAT、study 消费者只领
        AUTOMATION（3c kind 归属），默认 None = 全部。
        """
        return self.controller.run_pending(kinds)

    # ── 观测 ─────────────────────────────────────────────

    def drain_events(self) -> list[tuple[str, dict]]:
        events, self._events = self._events, []
        return events

    def verify_invariants(self) -> None:
        from strategy_research.core.gateway.invariants import verify_all

        verify_all()

    def close(self) -> None:
        if self._store:
            self._store.close()
