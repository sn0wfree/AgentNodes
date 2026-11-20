# -*- coding: utf-8 -*-
"""隔离上下文 codec（B2.3 子批 3a · 零接线）——父/worker 之间唯一的上下文协议。

把自由透传的 ``ctx`` dict 收敛为显式白名单 schema：

- :data:`CTX_VERSION` 协议版本（wire 顶层 ``ctx_v``）；缺失/无效/不兼容
  显式报错——它是**漂移显式化锚**，不是新旧版本共存机制（方案 §4）；
- :func:`to_wire` / :func:`from_wire` 两个纯函数是唯一编解码入口，两端
  共用同一份校验；进程内对象/回调字段**从不出现在映射里**——泄漏由
  构造 impossibility 保证（方案 §2.3）；
- 只投影 ToolContext 的纯数据字段，不碰源结构（``core.py`` 不动）。

设计：docs/design/b23-context-crossing-r014-20261120.md（R-014 已批准 3a）。
本批零接线——只提供 codec + 契约测试，不改 isolation.py / worker 脚本 /
普通工具调用路径。3b 才把发送/接收两侧接上。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "CTX_VERSION",
    "IsolationContextError",
    "IsolationCtxWire",
    "WIRE_FIELDS",
    "from_wire",
    "to_wire",
    "validate_wire",
]

#: 当前 wire 协议版本。改 schema（增删字段/改类型）必须 +1 并评审。
CTX_VERSION = 1

#: wire 白名单字段（**唯一事实源**——契约测试逐字段断言键集合等于它）。
#: 9 个 ToolContext 纯数据字段；回调/进程内对象永不入列。
WIRE_FIELDS: frozenset[str] = frozenset({
    "workspace", "session_id", "strategy_dir", "runs_dir", "results_tsv",
    "write_roots", "read_roots", "message_id", "tool_call_id",
})

#: Path 类型字段（wire=str → ToolContext=Path）。
_PATH_FIELDS: frozenset[str] = frozenset({
    "workspace", "strategy_dir", "runs_dir", "results_tsv",
})

#: tuple 类型字段（wire=list → ToolContext=tuple）。
_TUPLE_FIELDS: frozenset[str] = frozenset({"write_roots", "read_roots"})


class IsolationContextError(ValueError):
    """上下文 schema 校验失败（版本 / 多余键 / 类型 / 路径）。"""


@dataclass(frozen=True)
class IsolationCtxWire:
    """wire 上的上下文 schema（字段即白名单，含版本）。"""

    ctx_v: int
    workspace: str | None = None
    session_id: str | None = None
    strategy_dir: str | None = None
    runs_dir: str | None = None
    results_tsv: str | None = None
    write_roots: tuple[str, ...] | None = None
    read_roots: tuple[str, ...] | None = None
    message_id: str | None = None
    tool_call_id: str | None = None


def _check_version(obj: dict[str, Any]) -> None:
    """校验 ``ctx_v``：缺失 / 非整型 / <=0 / >CTX_VERSION 都显式报错。"""
    if "ctx_v" not in obj:
        raise IsolationContextError("missing required 'ctx_v' (context schema version)")
    v = obj["ctx_v"]
    if isinstance(v, bool) or not isinstance(v, int):
        raise IsolationContextError(f"invalid 'ctx_v' type: {type(v).__name__} (need int)")
    if v <= 0:
        raise IsolationContextError(f"invalid 'ctx_v' value: {v} (must be positive)")
    if v > CTX_VERSION:
        raise IsolationContextError(
            f"incompatible 'ctx_v': {v} > supported {CTX_VERSION}"
        )


def to_wire(ctx: Any) -> dict[str, Any]:
    """ToolContext → wire dict（发送侧）。

    只按 :data:`WIRE_FIELDS` 显式映射 9 个数据字段——回调 / 进程内对象
    字段（emit_* / permission_* / data_store / sandbox / before_execute）
    **从不读取**，所以它们不可能出现在 wire 上。版本键由 codec 注入，
    调用方无法伪造缺版本。

    类型：Path → str；tuple → list（JSON 序列化友好）；None 保留。
    """
    from agentnodes.kernel.agent.tools.core import ToolContext

    if not isinstance(ctx, ToolContext):
        raise IsolationContextError(
            f"to_wire expects ToolContext, got {type(ctx).__name__}"
        )

    wire: dict[str, Any] = {"ctx_v": CTX_VERSION}
    for name in sorted(WIRE_FIELDS):
        value = getattr(ctx, name, None)
        if name in _PATH_FIELDS and value is not None:
            value = str(value)
        elif name in _TUPLE_FIELDS and value is not None:
            value = list(value)
        wire[name] = value
    return wire


def validate_wire(obj: dict[str, Any]) -> None:
    """校验 wire dict（纯函数：不改 obj、不构造 ToolContext）。

    校验顺序与异常消息与 :func:`from_wire` 逐字一致：
    ① dict 类型；② ``ctx_v`` 缺失/无效/不兼容；③ 白名单外键（报错含键名）；
    ④ 路径字段绝对性 + ``..`` 段检查；⑤ 各字段 wire 类型。

    存在的理由（第三轮复审候选 I3）：父侧公开入口 ``isolate_tool_call``
    需在 spawn 前校验 versioned-dict 形态——「坏 ctx 不出父进程」的宣称
    要落在承载它的 interface 上。worker 侧 :func:`from_wire` 复用本函数，
    保留纵深防御（拒收不毒化），二者规则同源、不会漂移。

    不做语义级穿越判定（相对 workspace 越界归 PathWhitelist 单一职责）。
    """
    if not isinstance(obj, dict):
        raise IsolationContextError(
            f"wire ctx must be dict, got {type(obj).__name__}"
        )
    _check_version(obj)

    extra = set(obj) - WIRE_FIELDS - {"ctx_v"}
    if extra:
        raise IsolationContextError(
            f"unexpected wire ctx keys: {sorted(extra)} "
            f"(callbacks / in-process objects must not cross the boundary)"
        )

    for name in WIRE_FIELDS:
        value = obj.get(name)
        if value is None:
            continue
        if name in _PATH_FIELDS:
            if not isinstance(value, str):
                raise IsolationContextError(
                    f"{name} must be str on wire, got {type(value).__name__}"
                )
            path = Path(value)
            if not path.is_absolute() or ".." in path.parts:
                raise IsolationContextError(
                    f"{name} must be an absolute path without '..': {value!r}"
                )
        elif name in _TUPLE_FIELDS:
            if not isinstance(value, (list, tuple)) or not all(
                isinstance(item, str) for item in value
            ):
                raise IsolationContextError(
                    f"{name} must be a list of str on wire, got {value!r}"
                )
        else:
            if not isinstance(value, str):
                raise IsolationContextError(
                    f"{name} must be str or None on wire, got {type(value).__name__}"
                )


def from_wire(obj: dict[str, Any]) -> Any:
    """wire dict → ToolContext（worker 侧唯一解码入口）。

    先经 :func:`validate_wire` 完整校验（异常类型/消息逐字不变），再做值
    强转（str→Path、list→tuple）构造 ToolContext；缺失字段 → None（与
    ToolContext 默认一致）。

    不做语义级穿越判定（相对 workspace 越界归 PathWhitelist 单一职责）。
    """
    from agentnodes.kernel.agent.tools.core import ToolContext

    validate_wire(obj)

    values: dict[str, Any] = {}
    for name in WIRE_FIELDS:
        value = obj.get(name)
        if value is None:
            values[name] = None
        elif name in _PATH_FIELDS:
            values[name] = Path(value)
        elif name in _TUPLE_FIELDS:
            values[name] = tuple(value)
        else:
            values[name] = value

    return ToolContext(**values)
