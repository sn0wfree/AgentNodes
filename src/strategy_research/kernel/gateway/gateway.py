"""Gateway 组装（阶段 3b）—— 单进程内核的可运行形态。

阶段 3b 升级：
- engine_factory 从 monkey-patch 升级为构造参数
- 集成 PlatformStore（事件持久化 + blob + artifact 登记）
- 灰度 flag 默认开（旧路径保留但 deprecated）
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from strategy_research.kernel.gateway.fencing import SessionFence
from strategy_research.kernel.gateway.session_controller import RunResult, SessionController
from strategy_research.kernel.orchestration import EntryPoint

logger = logging.getLogger(__name__)


def _resolve_workspace() -> Path:
    """workspace 根（P-5 指针的 URI 根）：SR_WORKSPACE_PATH 或 cwd。"""
    import os

    env = os.environ.get("SR_WORKSPACE_PATH")
    return Path(env) if env else Path.cwd()


def _mailbox_config() -> Any:
    """读 mailbox 段配置（cap / steer_cap / aging_s）。

    装载失败一律回落到 dataclass 默认值——**但**只有显式配置了非法值时才
    会抛 ConfigError；那种情况按 fail-fast 直接冒泡（配错不能静默按默认跑）。
    """
    from strategy_research.kernel.orchestration.config_hub import load_platform_config

    return load_platform_config().mailbox


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
        max_worker_slots: int | None = None,
        ticket_sink: Any | None = None,
    ) -> None:
        self._events: list[tuple[str, dict]] = []
        # 阶段一 §2.8：Worker Slot 管理器——订阅 assignment.report 管理
        # Worker 占用。只订阅事件、不读 Ticket 表（kernel 不得 import apps）。
        from strategy_research.kernel.gateway.worker_slots import WorkerSlotManager

        self._worker_slots = WorkerSlotManager(max_slots=max_worker_slots)
        # 阶段一：Ticket/Assignment 持久化钩子。None = 不启用（测试路径）。
        # 生产由装配根注入 apps.study.ticket_sink.TicketSink —— kernel 只认
        # duck-typed 接口（admit/settle/recover），不 import apps（H1）。
        self._ticket_sink = ticket_sink
        # R7：外部 sink 集合（替代原链式 _external_sink）+ 代际表。
        self._sinks: set[Callable[[str, dict], None]] = set()
        self._sink_lock = threading.Lock()
        self._turn_gen: dict[str, int] = {}      # session_id -> 已入队轮数
        self._turn_done_gen: dict[str, int] = {} # session_id -> 已完成轮数
        # 构造时的 sink 进集合（与 add_sink 同路径，语义一致）。
        if sink is not None:
            self._sinks.add(sink)
        combined = self._combined_sink
        # R2.2：fence 先建——EntryPoint 的 steer 路由用它查活跃 run。
        self.fence = SessionFence()
        # B1：活跃 run 的 CancelScope 注册表（物理 session id -> scope）。
        self._cancel_scopes: dict[str, Any] = {}
        # R2.3/E2：mailbox 的 cap / steer_cap / aging_s 来自平台配置
        # （QNODES_MAILBOX__CAP 等）。此前声明了却从未传入——配置静默失效，
        # 恒用 mailbox.py 的模块默认值。装载失败按 fail-fast 处理（配错必须
        # 报错，见 config_hub 契约），但仅在显式配置了 env/file 时才可能抛。
        mail_cfg = _mailbox_config()
        self.entry = EntryPoint(
            sink=combined,
            cap=mail_cfg.cap,
            steer_cap=mail_cfg.steer_cap,
            aging_s=mail_cfg.aging_s,
            is_session_active=self.fence.active_run,
            # B1：interrupt 工单在有活跃 run 时触发取消（定稿语义）。
            on_interrupt=self._cancel_active_run,
        )
        self.controller = SessionController(
            self.entry, client=client, system=system, sink=combined,
            engine_factory=engine_factory,
            # R1：写栅栏注入——控制器 settle 前过 fence（INV-4），
            # 认领即超越（INV-5）。同一实例供 verify_invariants 检查。
            fence=self.fence,
            # B1：取消注册表——控制器把每次 run 的 CancelScope 挂上去，
            # interrupt/cancel_session 据此触发取消。
            cancel_registry=self._cancel_scopes,
            # P5-1b：有 registry 时控制器默认图用 react 节点（无工具调用时
            # 退化为单次 LLM），否则纯 llm 节点。
            registry=registry, stream=stream,
            # B5 R-1（选项 A）：组装根构造 ContextHub 并注入。None = 无上下
            # 文注入——直构 controller 的测试路径行为不变。
            context_hub=context_hub,
            context_policy=context_policy,
            # 阶段一：Ticket/Assignment 持久化——准入路径追加 upsert，
            # 退出点 settle。None = 不启用（测试/无业务包路径）。
            ticket_sink=self._ticket_sink,
        )
        # 阶段 3b：PlatformStore（事件持久化 + blob + artifact 登记）
        self._store = None
        if data_dir is not None:
            from strategy_research.foundation.storage.platform_store import PlatformStore

            self._store = PlatformStore(data_dir=data_dir)
        # R4：artifact 通道（P-3）——执行层的大结果落盘 + 登记，事件只带
        # 指针。显式注入优先；否则 store 可用时自建（workspace 为 URI 根）。
        # None = 现行为（测试/无 store 路径，事件内容逐字节不变）。
        self._artifacts = artifact_channel
        # 装配根需要的 workspace（基因组持久化等子系统据此定位落盘目录）。
        # 单独保存而非临时计算：接入方（L1 组装根）在 Gateway 构造后
        # 才执行，那时局部变量已不可见。
        self._workspace = workspace or _resolve_workspace()
        if self._artifacts is None and self._store is not None:
            ws = self._workspace
            try:
                from strategy_research.foundation.storage.artifact_channel import (
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
        self._turn_lock = threading.Lock()

    @property
    def store(self):
        """PlatformStore（可能为 None——data_dir 未配置时纯内存模式）。"""
        return self._store

    @property
    def artifacts(self):
        """P-3 artifact 通道（可能为 None——store 未配置时）。"""
        return self._artifacts

    @property
    def worker_slots(self):
        """阶段一 §2.8：Worker Slot 管理器（订阅 assignment.report）。"""
        return self._worker_slots

    # ── 装配根注入口（构造后接线） ───────────────────────
    #
    # 三个子系统（Ticket/Assignment、容量闸、基因组版本链）都需要在
    # Gateway 实例存在**之后**才能接上：TicketSink 的 emit 要落到
    # ``_combined_sink``，容量闸要挂在派发路径。构造期参数做不到这一点
    # （emit 会捕获未初始化的 Gateway）。
    #
    # 这些 setter 只接受 duck-typed 对象，不 import 任何 apps 模块——
    # 具体实现由 L1 组装根（``interfaces/gateway_wiring.py``）解析后注入。

    @property
    def ticket_sink(self):
        """阶段一：Ticket/Assignment 持久化钩子（未接线时为 None）。"""
        return self._ticket_sink

    def attach_ticket_sink(self, sink: Any) -> None:
        """接上 Ticket/Assignment 持久化（构造后）。

        同时转发给已构造的 SessionController——否则准入路径仍不落库，
        这正是本轮"接上了但没生效"的成因。
        """
        self._ticket_sink = sink
        if getattr(self, "controller", None) is not None:
            self.controller._ticket_sink = sink

    @property
    def capacity(self):
        """阶段二：容量组闸（未接线时为 None）。"""
        return getattr(self, "_capacity", None)

    def attach_capacity_gate(self, gate: Any) -> None:
        """接上容量闸（构造后），同时转发给控制器——准入发生在 run_pending。"""
        self._capacity = gate
        if getattr(self, "controller", None) is not None:
            self.controller._capacity = gate

    @property
    def genome(self):
        """阶段三：Tier 1 基因组版本链（未接线时为 None）。"""
        return getattr(self, "_genome", None)

    def attach_genome_store(self, store: Any) -> None:
        """接上基因组版本链（构造后）。装配根负责保活。"""
        self._genome = store

    def _combined_sink(self, kind: str, data: dict) -> None:
        self._default_sink(kind, data)
        # 阶段一 §2.8：Worker Slot 订阅——只订阅事件，不读 Ticket 表。
        # 放在默认 sink 之后、外部 sink 之前：slot 状态先更新，外部
        # 观察者看到的 slot 表已是本事件的后果。
        if self._worker_slots is not None and kind == "assignment.report":
            try:
                self._worker_slots.handle_report(data)
            except Exception:  # noqa: BLE001 —— slot 记账失败不得吞事件
                logger.warning(
                    "worker slot update failed for %s",
                    data.get("assignment_id"), exc_info=True,
                )
        # R7：广播给所有外部 sink（集合快照遍历，防并发修改）。
        with self._sink_lock:
            sinks = tuple(self._sinks)
        for sink in sinks:
            try:
                sink(kind, data)
            except Exception:  # noqa: BLE001 —— 单个 sink 失败不影响其他
                logger.debug("external sink failed: %s", kind, exc_info=True)
        # R2.1b：有工单入队即唤醒消费者（无消费者时是 no-op set）。
        if kind == "work.enqueued":
            self._consumer_wake.set()
        # R7：代际推进——wait_turn 据此区分「上一轮」与「这一轮」。
        if kind == "work.enqueued":
            sid = str(data.get("session_id") or "")
            if sid:
                with self._sink_lock:
                    self._turn_gen[sid] = self._turn_gen.get(sid, 0) + 1

    def add_sink(self, sink: Callable[[str, dict], None]) -> Callable[[], None]:
        """注册外部 sink，返回**注销函数**（幂等）——R7 修复 sink 累积。

        此前 `install_sink` 每调用一次就把前一个 sink 包一层闭包，永不释放：
        每个 `/chat/send` 都调一次 → 链长随请求数线性增长；更严重的是旧
        bridge 闭包捕获旧请求的 message_id，同一 session 第二次请求时旧
        bridge 仍存活 → **重复 SSE 事件**（前端出现重复文本）。

        改为集合 + 锁：注册/注销 O(1)，广播遍历快照。sink 之间互不影响，
        互不嵌套。
        """
        with self._sink_lock:
            self._sinks.add(sink)

        def unsubscribe() -> None:
            with self._sink_lock:
                self._sinks.discard(sink)

        return unsubscribe

    def install_sink(self, sink: Callable[[str, dict], None]) -> Callable[[], None]:
        """[兼容别名] 等价于 add_sink，返回注销函数。

        消费者必须调用返回的函数（或由 bridge 在收尾时自注销），
        否则仍是累积——新代码请直接用 ``add_sink``。
        """
        return self.add_sink(sink)

    # ── B1：取消 ─────────────────────────────────────────

    def _cancel_active_run(self, session_id: str, reason: str = "interrupt") -> bool:
        """置该 session 活跃 run 的取消位（interrupt 语义）。无活跃 run → False。"""
        scope = self._cancel_scopes.get(session_id)
        if scope is None:
            return False
        scope.cancel(reason=reason)
        return True

    def cancel_session(self, session_id: str, *, reason: str = "user") -> bool:
        """对外取消 API：取消该 session 的活跃 run（幂等）。

        返回 True=已发出取消信号；False=当前无活跃 run（不报错）。
        """
        return self._cancel_active_run(session_id, reason)

    # ── E2：配置指纹进事件流 ─────────────────────────────

    def emit_config_fingerprint(self) -> str | None:
        """把平台配置的 sha256 指纹写入事件流（audit 对账用）。

        指纹经 ``SENSITIVE_KEYS`` 剔除敏感字段（当前 PlatformConfig 不含
        密钥字段，机制保留）；事件只带指纹哈希，不带配置内容。
        失败降级返回 None，不阻断启动。
        """
        try:
            from strategy_research.kernel.orchestration.config_hub import (
                config_fingerprint,
                load_platform_config,
            )

            fp = config_fingerprint(load_platform_config())
            self._combined_sink("config.fingerprint", {"fingerprint": fp})
            return fp
        except Exception:  # noqa: BLE001 —— audit 事件失败不阻断启动
            logger.debug("config fingerprint emit failed", exc_info=True)
            return None

    # ── B3：状态查询 ─────────────────────────────────────

    def session_status(self, session_key: str) -> dict[str, Any]:
        """非阻塞状态快照（receipt 契约的 poll 端）。

        全部读已有内存状态，无新状态机。重启后（B2 recover）last_* 为空、
        active=False——诚实反映「恢复状态不恢复执行」。
        """
        session_id = self.entry.session_id_for(session_key) or ""
        # fence 域 = 物理 session id（剥 kind 前缀，R3 约定）
        domain = session_key.split(":", 1)[-1]
        run_id = str(self.fence.active_run(domain) or "")
        last = self._turn_results.get(session_id) if session_id else None
        return {
            "session_key": session_key,
            "session_id": session_id,
            "active": bool(run_id),
            "active_run_id": run_id,
            "queued": (self.entry.mailbox_for(session_key).peek_depth()
                       if self.entry.mailbox_for(session_key) is not None else 0),
            "last_turn_id": last.turn_id if last else "",
            "last_ok": (last.ok if last else None),
            "last_answer": (last.answer if last else ""),
            "last_error": (last.error if last else None),
        }

    # ── B2：fold 重建 ────────────────────────────────────

    _TERMINAL_RUN_EVENTS = ("run.settled", "run.failed", "run.interrupted")

    def recover(self) -> dict[str, int]:
        """从事件流重建内存状态（fold），并恢复待办 Assignment。幂等。

        恢复（「恢复状态，不恢复执行」）：
        - turn 计数器：下一 turn 续号，不与历史撞号（P-4）；
        - session_key → uuid 映射：同 key 提交落回同一 aggregate；
        - in-flight run：`run.started` 无 terminal 配对 → 发
          `run.interrupted`（重试靠重提，不自动续跑——auto_resume=false）；
        - **待办 Assignment**：从 tickets/assignments 表取非终态项
          （pending/running/waiting/ready/blocked），重新纳入 Worker Slot
          记账并 emit `assignment.report`（status=blocked，reason=restart）。

        不恢复（定稿明说）：mailbox 待办（payload 不在事件流，重试靠
        重提）、幂等表、steer 队。无 store 时 no-op。

        **为什么待办 Assignment 要单独恢复**：事件流能 fold 出 turn 计数，
        但 Tickets/Assignments 是**独立持久化实体**（施工方案 §2 的核心
        设计）。进程崩溃时 `run.started` 已被 fold 成 `run.interrupted`，
        可 Assignment 行仍是 pending/running——不恢复的话它就永远卡在
        那个状态，既没有 Worker 占用、也不再被任何路径推进，成为孤儿。

        ``stats`` 里的 ``assignments`` 键**只在启用 ticket_sink 时出现**：
        未启用 Ticket 模型的调用方（纯事件流 fold）看到的字典与此前
        逐字一致，不破坏既有契约。
        """
        stats: dict[str, int] = {
            "sessions": 0, "turns": 0, "interrupted": 0,
        }
        if self._store is None:
            # 无事件库时仍尝试恢复 Assignment：它们是独立持久化实体，
            # 存在自己的表里，不依赖事件流。
            if self._ticket_sink is not None:
                stats["assignments"] = self._recover_pending_assignments()
            return stats

        for sid in self._store.iter_session_ids():
            if not sid:
                continue
            events = self._store.replay(sid, kinds=[
                "turn.admitted", "run.started",
                "run.settled", "run.failed", "run.interrupted",
            ])
            if not events:
                continue
            stats["sessions"] += 1
            max_turn = 0
            session_key: str | None = None
            started: dict[str, bool] = {}
            terminal: set[str] = set()
            for ev in events:
                if ev.kind == "turn.admitted":
                    # turn_id = "{uuid}#{n}"
                    _, _, suffix = str(ev.data.get("turn_id", "")).rpartition("#")
                    if suffix.isdigit():
                        max_turn = max(max_turn, int(suffix))
                    sk = ev.data.get("session_key")
                    if isinstance(sk, str) and sk:
                        session_key = sk
                elif ev.kind == "run.started":
                    rid = str(ev.data.get("run_id") or "")
                    if rid:
                        started[rid] = True
                elif ev.kind in self._TERMINAL_RUN_EVENTS:
                    rid = str(ev.data.get("run_id") or "")
                    if rid:
                        terminal.add(rid)
            if max_turn:
                self.controller._turn_counters[sid] = max_turn
                stats["turns"] += 1
            if session_key:
                self.entry.restore_session(session_key, sid)
            for rid in started:
                if rid not in terminal:
                    self._store.emit(sid, "run.interrupted", {
                        "run_id": rid, "reason": "restart",
                    })
                    stats["interrupted"] += 1

        if self._ticket_sink is not None:
            stats["assignments"] = self._recover_pending_assignments()
        return stats

    def _recover_pending_assignments(self) -> int:
        """把非终态 Assignment 重新纳入记账（崩溃恢复）。

        返回恢复条数。sink 为 None 时返回 0（未启用 Ticket 模型）。

        **非终态定义**：pending / running / waiting / ready / blocked。
        failed / dropped / ended 是终态，不需要恢复。

        恢复动作（**只恢复状态，不恢复执行**）：
        - 把待办项标 ``blocked``（reason=restart）并 emit
          ``assignment.report`` —— 它们没失败，只是被重启打断了；
          标 failed 会污染失败率统计。
        - 重新纳入 Worker Slot 记账（blocked 不占 slot 位，但在
          ``active()`` 里可见，审计视图能看到"这些还在路上"）。

        优先调用 sink 的 ``recover_and_mark``（一次性完成标注 + emit）；
        只有 ``recover`` 的旧实现退化为纯读取 + 本地记账。
        """
        if self._ticket_sink is None:
            return 0

        pending: list[Any] = []
        try:
            if hasattr(self._ticket_sink, "recover_and_mark"):
                pending = list(self._ticket_sink.recover_and_mark(reason="restart"))
            else:
                pending = list(self._ticket_sink.recover())
        except Exception as exc:  # noqa: BLE001 —— 恢复失败不阻断启动
            logger.warning("pending assignment recovery failed: %s", exc)
            return 0

        for item in pending:
            try:
                self._worker_slots.track_pending(
                    getattr(item, "assignment_id", ""),
                    run_id=getattr(item, "run_id", ""),
                    ticket_id=getattr(item, "ticket_id", ""),
                )
            except Exception:  # noqa: BLE001
                logger.debug("slot bookkeeping failed for %r", item, exc_info=True)
        return len(pending)

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
        """run_once + 按 session 记录结果并推进完成代际（wait_turn 的产出端）。"""
        for result in self.run_once(kinds):
            sid = result.turn_id.split("#", 1)[0]
            with self._turn_lock:
                # A3：只保留最近一次，不再无限累积
                self._turn_results[sid] = result
                self._turn_done_gen[sid] = self._turn_done_gen.get(sid, 0) + 1
            done = self._turn_done.setdefault(sid, threading.Event())
            done.set()

    def consumer_running(self) -> bool:
        return bool(
            self._consumer_thread is not None and self._consumer_thread.is_alive()
        )

    def wait_turn(self, session_id: str, timeout: float | None = None) -> RunResult | None:
        """等待该 session **本次提交之后**完成的下一个 turn。

        只等待、不驱动——执行始终是消费者的事。超时返回 None。

        R7 修复：此前用「事件已 set」判断，事件永不 clear，同一 session
        第二次调用会立即返回上一轮的 RunResult。现改为**代际比较**——
        记录调用时刻的完成代际，等待它前进。
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._turn_lock:
            start_gen = self._turn_done_gen.get(session_id, 0)
        while True:
            done = self._turn_done.setdefault(session_id, threading.Event())
            remaining = (
                None if deadline is None else max(0.0, deadline - time.monotonic())
            )
            if deadline is not None and remaining == 0.0:
                return None
            done.wait(remaining)
            with self._turn_lock:
                if self._turn_done_gen.get(session_id, 0) > start_gen:
                    result = self._turn_results.get(session_id)
                    # 新一轮已到：清事件，让下一次 wait 重新阻塞
                    done.clear()
                    return result
            if deadline is not None and time.monotonic() >= deadline:
                return None

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
        from strategy_research.kernel.gateway.invariants import verify_all

        verify_all()

    def close(self) -> None:
        if self._store:
            self._store.close()
