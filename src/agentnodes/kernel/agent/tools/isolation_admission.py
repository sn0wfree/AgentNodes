# -*- coding: utf-8 -*-
"""隔离准入 policy（L3 深模块）——四级能力声明→有效能力的判定点。

单一公开面：:func:`check_isolation_admission`（能否声明 isolatable）与
:func:`effective_capability`（读声明 + 非法值回落 + 准入回落后的实际有效
能力）。四级能力常量是本模块的公共词汇，由 core.py re-export 给 loop
消费者，保持 import 面稳定（第二轮复审候选 1+2 的深化；见 GLOSSARY.md
的 IsolationAdmission 词条）。

设计：docs/design/admission-module-extraction-r014-20261120.md
"""
from __future__ import annotations

from typing import Any

# ── 超时能力声明（批 4a · 路线步骤 4 · 裁决见
# docs/design/gateway-timeout-and-strict-history-design-20261118.md §10）──
# 四级是**机制属性，非强弱排序**：abandonable 与 unstoppable 都可能继续
# 执行并产生副作用，区别是等待方是否可放弃。声明 ≠ 事实：事件按每次
# 调用的实际状态（stopped/still_running/returned/unknown）记录，不由
# 本声明推断。
TIMEOUT_CAPABILITY_CANCELLABLE = "cancellable"
TIMEOUT_CAPABILITY_ABANDONABLE = "abandonable"
TIMEOUT_CAPABILITY_ISOLATABLE = "isolatable"
TIMEOUT_CAPABILITY_UNSTOPPABLE = "unstoppable"
TIMEOUT_CAPABILITIES = frozenset({
    TIMEOUT_CAPABILITY_CANCELLABLE,
    TIMEOUT_CAPABILITY_ABANDONABLE,
    TIMEOUT_CAPABILITY_ISOLATABLE,
    TIMEOUT_CAPABILITY_UNSTOPPABLE,
})

# 4c-B2.1 准入门槛：声明 isolatable 的工具必须只含可在进程终止边界内
# 结束的副作用。DB / NET 操作已发出的请求无法靠终止本地进程撤销
# （契约 §10.4：需服务端确认取消 + 禁止截止后提交，尚未实现）——
# 声明 isolatable + DB/NET effects 属过度承诺，准入检查拒绝。
_ISOLATION_FORBIDDEN_EFFECTS = frozenset({"db", "net"})

# B2.5 5a：注入参数 superset（以 _build_tool_kwargs 实际注入面为事实源，
# 不复用已过时的 _INJECTED_PARAMS——后者缺 emit_event/_subagent_count_ref/
# _parent_registry/message_id）。回调字段永不跨界（B2.3 codec 构造级保证），
# 依赖它们的工具在 worker 内会静默分叉，故 isolatable 声明必须拒绝。
ISO_INJECTED_KWARGS: frozenset[str] = frozenset({
    "workspace", "session_id", "_progress_callback", "emit_event",
    "message_id", "_subagent_count_ref", "_parent_registry",
})
_ISO_CALLBACK_ATTRS: frozenset[str] = frozenset({
    "emit_progress", "emit_event", "permission_evaluator",
    "permission_gateway", "data_store", "sandbox", "before_execute",
})


def _scan_isolation_callback_deps(execute: Any) -> str | None:
    """B2.5 5a C2/C3：静态扫描 execute 是否读回调/kwargs 注入面。

    - C2 签名面：显式声明 ISO_INJECTED_KWARGS 中的参数 → 拒（**kwargs 形态
      转 C3，不因"未显式声明"豁免）；
    - C3 函数体面：AST 读 `ctx.<回调字段>` 或 `kwargs.get/[]` 注入键 → 拒；
    - `inspect.getsource` / AST 解析失败 → **fail-closed 拒**（不静默放行）。

    Returns None = 无分叉依赖；非空 = 拒绝理由。静态扫描非形式化证明——
    别名/反射可能绕过，C4 显式 allowlist 才是最终执行依据（方案 §2.1）。
    """
    import ast
    import inspect
    import textwrap

    try:
        sig = inspect.signature(execute)
    except (TypeError, ValueError):
        return "isolatable 声明拒绝：execute 签名不可解析（fail-closed）"
    explicit = set(sig.parameters)
    declared = explicit & ISO_INJECTED_KWARGS
    if declared:
        return f"isolatable 声明拒绝：execute 显式依赖注入参数 {sorted(declared)}"

    try:
        src = textwrap.dedent(inspect.getsource(execute))  # 类方法源码带缩进
        tree = ast.parse(src)
    except (OSError, TypeError, SyntaxError) as exc:
        return f"isolatable 声明拒绝：execute 源码不可解析（fail-closed）：{exc}"

    for node in ast.walk(tree):
        # ctx.<callback>
        if isinstance(node, ast.Attribute):
            if (
                isinstance(node.value, ast.Name) and node.value.id == "ctx"
                and node.attr in _ISO_CALLBACK_ATTRS
            ):
                return f"isolatable 声明拒绝：execute 读 ctx.{node.attr}（worker 内恒 None，静默分叉）"
        # kwargs.get("emit_event") / kwargs["emit_event"]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "kwargs" and node.func.attr == "get"
                and node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in ISO_INJECTED_KWARGS
            ):
                return f"isolatable 声明拒绝：execute 读 kwargs.{node.args[0].value}（worker 不注入）"
        if isinstance(node, ast.Subscript):
            if (
                isinstance(node.value, ast.Name) and node.value.id == "kwargs"
                and isinstance(node.slice, ast.Constant)
                and node.slice.value in ISO_INJECTED_KWARGS
            ):
                return f"isolatable 声明拒绝：execute 读 kwargs[{node.slice.value!r}]（worker 不注入）"
    return None


def check_isolation_admission(tool: Any) -> str | None:
    """检查工具声明 ``isolatable`` 是否满足准入门槛（B2.1 C1 + B2.5 C2/C3）。

    Returns: None = 可声明；非空字符串 = 拒绝理由（声明应回落 unstoppable）。
    不执行工具、不改运行路径，也不保证声明等于事实（契约 §10.3）。
    扫描是**纯函数**——无全局缓存、无 execute 强引用（第二轮候选 2）。

    门槛：
    - C1 effects：DB / NET 进程终止无法撤销 → 拒；
    - C2/C3 分叉面：execute 读回调 / kwargs 注入面 → 拒（worker 内静默
      分叉）；源码不可解析 → fail-closed 拒。
    """
    cap = getattr(tool, "timeout_capability", TIMEOUT_CAPABILITY_UNSTOPPABLE)
    if cap != TIMEOUT_CAPABILITY_ISOLATABLE:
        return None
    effects = set(getattr(tool, "effects", ()) or ())
    forbidden = effects & _ISOLATION_FORBIDDEN_EFFECTS
    if forbidden:
        return (
            f"isolatable 声明拒绝：effects 含 {sorted(forbidden)}——"
            "进程终止不能撤销已受理的远端/DB 操作（需服务端确认取消）"
        )
    execute = getattr(tool, "execute", None)
    if execute is None:
        return "isolatable 声明拒绝：无 execute 可审查（fail-closed）"
    return _scan_isolation_callback_deps(execute)  # 纯扫描，无缓存


def effective_capability(tool: Any) -> str:
    """工具的**有效**超时能力（声明 + 非法值回落 + 准入回落，Q6）。

    - ``tool is None``（未注册）→ unstoppable；
    - 声明非法值 → unstoppable（诚实默认）；
    - 声明 isolatable 但准入不通过 → unstoppable；
    - 其余（cancellable/abandonable/合法 isolatable）→ 原声明。

    声明 ≠ 事实：本函数只回"有效声明面"，不推断实际执行状态。
    """
    if tool is None:
        return TIMEOUT_CAPABILITY_UNSTOPPABLE
    cap = getattr(tool, "timeout_capability", TIMEOUT_CAPABILITY_UNSTOPPABLE)
    if cap not in TIMEOUT_CAPABILITIES:
        return TIMEOUT_CAPABILITY_UNSTOPPABLE
    if cap == TIMEOUT_CAPABILITY_ISOLATABLE and check_isolation_admission(tool) is not None:
        return TIMEOUT_CAPABILITY_UNSTOPPABLE
    return cap


__all__ = [
    "TIMEOUT_CAPABILITY_CANCELLABLE",
    "TIMEOUT_CAPABILITY_ABANDONABLE",
    "TIMEOUT_CAPABILITY_ISOLATABLE",
    "TIMEOUT_CAPABILITY_UNSTOPPABLE",
    "TIMEOUT_CAPABILITIES",
    "check_isolation_admission",
    "effective_capability",
]
