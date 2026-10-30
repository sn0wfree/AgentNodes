"""Gateway 组装（阶段 3b）—— 单进程内核的可运行形态。

阶段 3b 升级：
- engine_factory 从 monkey-patch 升级为构造参数
- 集成 PlatformStore（事件持久化 + blob + artifact 登记）
- 灰度 flag 默认开（旧路径保留但 deprecated）
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable

from strategy_research.core.gateway.fencing import SessionFence
from strategy_research.core.gateway.session_controller import RunResult, SessionController
from strategy_research.core.orchestration import EntryPoint

logger = logging.getLogger(__name__)


def _resolve_workspace() -> Path:
    """workspace 根（P-5 指针的 URI 根）：SR_WORKSPACE_PATH 或 cwd。"""
    import os

    env = os.environ.get("SR_WORKSPACE_PATH")
    return Path(env) if env else Path.cwd()


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
        artifact_channel: Any | None = None,
        workspace: str | Path | None = None,
    ) -> None:
        self._events: list[tuple[str, dict]] = []
        self._external_sink = sink
        combined = self._combined_sink
        # R2.2：fence 先建——EntryPoint 的 steer 路由用它查活跃 run。
        self.fence = SessionFence()
        self.entry = EntryPoint(
            sink=combined, is_session_active=self.fence.active_run,
        )
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
        # R4：artifact 通道（P-3）——执行层的大结果落盘 + 登记，事件只带
        # 指针。显式注入优先；否则 store 可用时自建（workspace 为 URI 根）。
        # None = 现行为（测试/无 store 路径，事件内容逐字节不变）。
        self._artifacts = artifact_channel
        if self._artifacts is None and self._store is not None:
            ws = workspace or _resolve_workspace()
            try:
                from strategy_research.core.storage.artifact_channel import (
                    ArtifactChannel,
                )

                self._artifacts = ArtifactChannel(self._store, ws)
            except Exception:  # noqa: BLE001 —— 通道不可用不阻断 chat
                logger.debug("artifact channel unavailable", exc_info=True)
        # 保持兼容：_build_engine 仍可访问（A1 preset 注册表会替换）
        # R2.1b：常驻消费者状态（start_consumer 显式启动，构造不启动——
        # 测试/直构路径零影响）。
        self._consumer_thread: threading.Thread | None = None
        self._consumer_wake = threading.Event()
        self._consumer_stop = threading.Event()
        self._turn_results: dict[str, RunResult] = {}  # session_id -> 最近一次
        self._turn_done: dict[str, threading.Event] = {}

    @property
    def store(self):
        """PlatformStore（可能为 None——data_dir 未配置时纯内存模式）。"""
        return self._store

    @property
    def artifacts(self):
        """P-3 artifact 通道（可能为 None——store 未配置时）。"""
        return self._artifacts

    def _combined_sink(self, kind: str, data: dict) -> None:
        self._default_sink(kind, data)
        if self._external_sink is not None:
            self._external_sink(kind, data)
        # R2.1b：有工单入队即唤醒消费者（无消费者时是 no-op set）。
        if kind == "work.enqueued":
            self._consumer_wake.set()

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

    # ── R2.1b：常驻消费者 ─────────────────────────────────

    def start_consumer(
        self, *, kinds: Any = None, poll_interval: float = 0.2,
    ) -> None:
        """后台 daemon 线程循环 run_once(kinds)。

        幂等：已在跑则 no-op。构造不启动——由宿主（API lifespan）显式
        启动，测试/直构路径保持同步语义。唤醒 = work.enqueued 置位 +
        poll_interval 兜底轮询；循环体就是现有 run_once，零新执行语义。
        """
        if self._consumer_thread is not None and self._consumer_thread.is_alive():
            return
        self._consumer_stop.clear()

        def _loop() -> None:
            while not self._consumer_stop.is_set():
                try:
                    self._consume_once(kinds)
                except Exception:  # noqa: BLE001 —— 单轮失败不杀消费者
                    logger.exception("gateway consumer iteration failed")
                self._consumer_wake.wait(poll_interval)
                self._consumer_wake.clear()

        self._consumer_thread = threading.Thread(
            target=_loop, name="gateway-consumer", daemon=True,
        )
        self._consumer_thread.start()

    def _consume_once(self, kinds: Any) -> None:
        """run_once + 按 session 记录结果（wait=true 等待原语的产出端）。"""
        for result in self.run_once(kinds):
            sid = result.turn_id.split("#", 1)[0]
            self._turn_results[sid] = result
            done = self._turn_done.setdefault(sid, threading.Event())
            done.set()

    def consumer_running(self) -> bool:
        return bool(
            self._consumer_thread is not None and self._consumer_thread.is_alive()
        )

    def wait_turn(self, session_id: str, timeout: float | None = None) -> RunResult | None:
        """等待该 session 最近提交的 turn 完成（E1=C 的等待原语）。

        只等待、不驱动——执行始终是消费者的事。超时返回 None。
        """
        done = self._turn_done.get(session_id)
        if done is None:
            # 提交后还没有任何结果：注册事件再等（防 check-then-event 竞态）
            done = self._turn_done.setdefault(session_id, threading.Event())
        if not done.wait(timeout):
            return None
        return self._turn_results.get(session_id)

    def stop_consumer(self, grace: float = 5.0) -> None:
        """置停止位并等待在跑 turn 收尾（grace 秒）；线程不可硬杀。"""
        self._consumer_stop.set()
        self._consumer_wake.set()
        thread = self._consumer_thread
        if thread is not None and thread.is_alive():
            thread.join(grace)

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
