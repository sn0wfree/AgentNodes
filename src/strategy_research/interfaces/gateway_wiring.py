"""Gateway 的平台级装配（组装根）。

**为什么这个模块在 interfaces（L1）**：它需要同时知道 kernel 的构造参数
和 apps 的具体实现类。让 kernel 自己去找 apps 就是 L3→L2 上行 import
（H1 违规）；让 apps 反过来配置 kernel 又会把入口语义塞进业务包。
组装根是唯一同时向上看到两者的位置，依赖方向天然是 L1 → L3 / L1 → L2。

**为什么装配是"尽力而为 + 可观测"**：任一子系统装配失败都不能让 chat
起不来。每个失败都发一条 ``<subsystem>.wiring_skipped`` 事件，把
"为什么没接上"留在事件流里——静默降级会让"功能没生效"变成无法诊断的
幽灵问题（正是本轮发现生产未接线的成因）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


def build_ticket_sink() -> Any | None:
    """构造 TicketSink（Ticket/Assignment 持久化）。

    经 ``CapabilityRegistry`` 解析实现类——kernel 侧的 registry 是 L3，
    apps 主动注册是 L2→L3，方向正确。解析失败返回 None（不启用）。
    """
    try:
        import strategy_research.apps  # noqa: F401 —— 触发注册副作用
        from strategy_research.kernel.agent.capability import resolve

        sink_cls = resolve("study.ticket_sink")
        return sink_cls()
    except Exception as exc:  # noqa: BLE001 —— 未接上不得阻断启动
        logger.debug("ticket_sink unavailable: %s", exc)
        return None


def build_capacity_gate() -> Any | None:
    """构造容量闸。纯 kernel 模块，不依赖 apps。"""
    try:
        from strategy_research.kernel.gateway.capacity import (
            CapacityConfig,
            CapacityGate,
        )

        return CapacityGate(CapacityConfig())
    except Exception as exc:  # noqa: BLE001
        logger.debug("capacity gate unavailable: %s", exc)
        return None


def build_genome_store(workspace: Any | None = None) -> Any | None:
    """构造 Tier 1 基因组版本链（**带持久化**）。

    有 workspace 时落盘到 ``<workspace>/.quantnodes/genome/``——这样进程
    重启后版本链还在，自我进化的成果不会随进程消失。无 workspace 时
    退化为纯内存（测试路径）。

    :param workspace: 持久化根；None = 纯内存
    """
    try:
        from strategy_research.kernel.orchestration.genome_version import (
            GenomeVersionStore,
        )

        store = None
        if workspace is not None:
            try:
                from pathlib import Path

                from strategy_research.foundation.storage.genome_store import (
                    GenomeStore,
                )

                store = GenomeStore(
                    Path(workspace) / ".quantnodes" / "genome"
                )
            except Exception as exc:  # noqa: BLE001 —— 落盘不可用则纯内存
                logger.warning("genome persistence unavailable: %s", exc)
                store = None
        return GenomeVersionStore(store=store)
    except Exception as exc:  # noqa: BLE001
        logger.debug("genome store unavailable: %s", exc)
        return None


def wire_gateway(gw: Any, *, emit: Callable[[str, dict], None] | None = None) -> dict[str, bool]:
    """把平台子系统接到已构造的 Gateway 上，返回各子系统接线结果。

    :param gw: ``Gateway`` 实例（已构造，未启动消费者）
    :param emit: 事件发射器；None 时用 ``gw`` 自身的 sink

    **执行顺序固定**（后者依赖前者）：
    0. apps 能力注册 —— 必须先于 1，否则 ``resolve("study.ticket_sink")``
       抛 ``CapabilityNotFound``。真实入口从不 import ``apps.capabilities``，
       所以注册副作用不会自己发生（本轮实测确认：registry 在 0 处停留）。
    1. ticket_sink —— 回填 emit，使 admit/settle 的事件进入事件流；
       Gateway 需要一个 setter 才能在构造后注入，见 ``Gateway.attach_ticket_sink``
    2. capacity gate —— Gateway 持有引用，派发准入时使用
    3. genome store —— RSI 版本链；装配根保活，避免被 GC 回收导致
       validation_gate 回滚时找不到链

    每个子系统独立 try：一个失败不影响其他。
    """
    emit = emit or getattr(gw, "_combined_sink", None)
    status: dict[str, bool] = {
        "capabilities": False,
        "ticket_sink": False,
        "capacity": False,
        "genome": False,
    }

    # ── 0. apps 能力注册 ─────────────────────────────────────────
    # 这是 1 的前提：TicketSink 经 registry 解析。注册是幂等的
    # （apps/__init__ 逐 kind 跳过已注册项）。
    try:
        import strategy_research.apps  # noqa: F401 —— 触发注册副作用
        from strategy_research.kernel.agent.capability import list_kinds

        status["capabilities"] = "study.ticket_sink" in list_kinds()
    except Exception as exc:  # noqa: BLE001
        logger.warning("capability registration failed: %s", exc)
        _emit_skip(emit, "capabilities", exc)

    # ── 1. TicketSink ────────────────────────────────────────────
    sink = getattr(gw, "ticket_sink", None) or build_ticket_sink()
    if sink is not None:
        try:
            if hasattr(sink, "bind_emit"):
                sink.bind_emit(emit)
            attach = getattr(gw, "attach_ticket_sink", None)
            if attach is not None:
                attach(sink)
                status["ticket_sink"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("ticket_sink wiring failed: %s", exc)
            _emit_skip(emit, "ticket_sink", exc)

    # ── 2. CapacityGate ─────────────────────────────────────────
    gate = getattr(gw, "capacity", None) or build_capacity_gate()
    if gate is not None:
        try:
            attach_capacity = getattr(gw, "attach_capacity_gate", None)
            if attach_capacity is not None:
                attach_capacity(gate)
                status["capacity"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("capacity gate wiring failed: %s", exc)
            _emit_skip(emit, "capacity", exc)

    # ── 3. GenomeVersion ────────────────────────────────────────
    # workspace 取自 Gateway（构造时已知）；没有则纯内存。
    genome = build_genome_store(getattr(gw, "_workspace", None))
    if genome is not None:
        try:
            # 登记到 kernel 的运行期位——**这是装配期 resolver 的唯一入口**。
            # 只挂到 Gateway 上不够：``skill_memory.resolve()`` 在 PromptBuilder
            # / ToolRegistry / CompactConfig 三个装配点被调用，它们手里没有
            # Gateway，只有这个进程级登记位能到达。
            from strategy_research.kernel.orchestration.genome_runtime import (
                set_active_genome,
            )

            set_active_genome(genome)
            attach_genome = getattr(gw, "attach_genome_store", None)
            if attach_genome is not None:
                attach_genome(genome)
            status["genome"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("genome store wiring failed: %s", exc)
            _emit_skip(emit, "genome", exc)

    if emit is not None:
        try:
            emit("gateway.wired", dict(status))
        except Exception:  # noqa: BLE001
            logger.debug("gateway.wired emit failed", exc_info=True)
    return status


def _emit_skip(emit: Any, subsystem: str, exc: BaseException) -> None:
    """把装配失败写进事件流——静默降级不可诊断。"""
    if emit is None:
        return
    try:
        emit(f"{subsystem}.wiring_skipped", {"error": str(exc)})
    except Exception:  # noqa: BLE001
        logger.debug("wiring_skipped emit failed", exc_info=True)


def _on_gateway_constructed(gw: Any) -> None:
    """``singleton.get_gateway()`` 的后置钩子实现。"""
    wire_gateway(gw)


#: 自注册：本模块被 import 即把钩子挂到 kernel 的构造路径上。
#:
#: 注册方向是 **L1 → L3**（interfaces 主动调 kernel 的函数），合法；
#: 反过来让 kernel import 本模块才是 L3→L1 上行违规。这也是为什么
#: ``kernel/gateway/singleton.py`` 里只有一个 ``register_wiring_hook``，
#: 没有任何 ``from strategy_research.interfaces...``。
def _register() -> None:
    try:
        from strategy_research.kernel.gateway.singleton import (
            register_wiring_hook,
        )

        register_wiring_hook(_on_gateway_constructed)
    except Exception:  # noqa: BLE001 —— 注册失败不阻断本模块可用
        logger.debug("wiring hook registration failed", exc_info=True)


_register()


__all__ = [
    "build_capacity_gate",
    "build_genome_store",
    "build_ticket_sink",
    "wire_gateway",
]
