"""LoopSpec — AgentLoop 构造参数的聚合体（批次 12 S2）。

**问题**：``AgentLoop.__init__`` 有 87 行参数（含注释），200+ 调用点各自
传一长串关键字——加一个参数要改签名、读一个构造点要扫 90 行。

**方案**：按职责分 6 组，每组一个 frozen dataclass。``AgentLoop.__init__``
新增 ``spec: LoopSpec | None = None`` 入口，**旧参数全部保留**（
``**legacy_kwargs`` 自动折叠进 spec）——200+ 调用点零改动。

## 六组划分

| 组 | 字段 | 为什么这样分 |
|---|---|---|
| **Budget（预算）** | max_iterations / no_progress_window / threshold_tokens / iteration_timeout_s / approval_timeout / approval_on_timeout / wrap_up_nudge / force_final_text | 全是「什么时候该停」的参数——B2 的 deciders 读它们 |
| **Runtime（运行时）** | stream_mode / heartbeat_interval / trace_dir / auto_git_commit / readonly | 执行形态与可观测性 |
| **Capability（能力）** | registry / memory / session_manager / circuit_breaker / retry_policy / compact_config / event_bus / hooks / injectors | 注入的「部件」 |
| **Context（上下文）** | system_prompt / allowed_tools / strategy / strategy_name / enable_goal_injection / enable_memory_injection / enable_hypothesis_auto_create | 影响 LLM 看到什么 |
| **Permission（权限）** | permission_evaluator / permission_gateway | Tier 1 闸门（批次 6-7 引入）|
| **Scope（作用域）** | workspace / strategy_dir / runs_dir / results_tsv / write_roots / read_roots / session_id / role | 运行在哪、谁的会话 |

**``config``（LLMConfig）不在 spec 内**：它是构造的**第一必需参数**，
放 spec 里会让 ``AgentLoop(spec)`` 与 ``AgentLoop(config, spec)`` 两种签名
并存，反而更乱。保持 ``__init__(self, config, registry, ..., spec=)``。

## 迁移策略（S2 只做加法）

1. 本批：`LoopSpec` 落地 + `__init__` 接受 `spec=` + `**legacy_kwargs`
   折叠。旧参数**一个不删**。
2. 阶段 3：所有调用点迁到 spec 后，删旧参数。

## 不做

- 不删任何旧参数（阶段 3）
- 不改 `config`/`registry` 位置（第一/第二必需参数）
- 不做 spec 的继承/组合链（扁平 6 组足够）
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

__all__ = [
    "BudgetSpec",
    "CapabilitySpec",
    "ContextSpec",
    "LoopSpec",
    "PermissionSpec",
    "RuntimeSpec",
    "ScopeSpec",
]


@dataclass
class BudgetSpec:
    """什么时候该停（B2 deciders 读这一组）。"""

    max_iterations: int = 10
    no_progress_window: int = 3
    threshold_tokens: int | None = None
    iteration_timeout_s: float | None = None
    approval_timeout: float = 1800.0
    approval_on_timeout: str = "continue"  # "continue" | "reject"
    wrap_up_nudge: bool = False
    force_final_text: bool = False


@dataclass
class RuntimeSpec:
    """执行形态与可观测性。"""

    stream_mode: bool = True
    heartbeat_interval: float = 15.0
    trace_dir: Path | None = None
    auto_git_commit: bool = False
    readonly: bool = False


@dataclass
class CapabilitySpec:
    """注入的部件（registry 除外——它是第二必需参数）。"""

    memory: Any | None = None
    session_manager: Any | None = None
    circuit_breaker: Any | None = None
    retry_policy: Any | None = None
    compact_config: Any | None = None
    event_bus: Any | None = None
    hooks: Any | None = None
    injectors: list[Any] | None = None


@dataclass
class ContextSpec:
    """影响 LLM 看到什么。"""

    system_prompt: str | None = None
    allowed_tools: list[str] | None = None
    strategy: Any | None = None
    strategy_name: str | None = None
    enable_goal_injection: bool = True
    enable_memory_injection: bool = True
    #: **批次 V3-a**：tier-1 tool_set 裁剪签名（空 = 不裁剪）。
    #: ``registry.get_definitions_for("")`` 与 ``get_definitions()`` 完全等价
    #: （``tools.py:881-882``），所以默认路径零变化。
    #: 这是原路径 B（make_react_node，V3-c 已删）相对路径 A 唯一多出的能力。
    task_signature: str = ""
    #: **批次 13 附录 §4**：hypothesis 子系统归档中，capability 未注册，
    #: 该开关永不生效——默认 False 以免谎报。恢复注册后翻回 True。
    enable_hypothesis_auto_create: bool = False
    enable_claim_validation: bool = False
    strict_claim_validation: bool = False
    role: str | None = None


@dataclass
class PermissionSpec:
    """Tier 1 权限闸门（批次 6-7）。"""

    permission_evaluator: Any | None = None
    permission_gateway: Any | None = None


@dataclass
class ScopeSpec:
    """运行在哪、谁的会话。"""

    workspace: Path | None = None
    strategy_dir: Path | None = None
    runs_dir: Path | None = None
    results_tsv: Path | None = None
    write_roots: tuple[str, ...] | None = None
    read_roots: tuple[str, ...] | None = None
    session_id: Any | None = None


@dataclass
class LoopSpec:
    """AgentLoop 构造参数的聚合体（6 组）。"""

    budget: BudgetSpec = field(default_factory=BudgetSpec)
    runtime: RuntimeSpec = field(default_factory=RuntimeSpec)
    capability: CapabilitySpec = field(default_factory=CapabilitySpec)
    context: ContextSpec = field(default_factory=ContextSpec)
    permission: PermissionSpec = field(default_factory=PermissionSpec)
    scope: ScopeSpec = field(default_factory=ScopeSpec)
    #: 事件出口（不在 6 组内——它是横切关注点，两处都要用）
    on_event: Any | None = None

    # ── 折叠：旧 kwargs → spec ────────────────────────────────────

    @classmethod
    def fold_legacy_kwargs(cls, kwargs: dict[str, Any]) -> "LoopSpec":
        """把旧式构造 kwargs 折叠进 spec（未知键抛 TypeError）。

        **200+ 调用点零改动的实现基础**：``AgentLoop.__init__`` 把
        ``**legacy_kwargs`` 交给本函数，已知键进 spec，未知键抛错
        （保持 Python 构造签名原有的严格性）。
        """
        spec = cls()
        groups = {
            "budget": spec.budget,
            "runtime": spec.runtime,
            "capability": spec.capability,
            "context": spec.context,
            "permission": spec.permission,
            "scope": spec.scope,
        }
        group_meta = {
            "budget": BudgetSpec,
            "runtime": RuntimeSpec,
            "capability": CapabilitySpec,
            "context": ContextSpec,
            "permission": PermissionSpec,
            "scope": ScopeSpec,
        }
        unknown: list[str] = []
        group_updates: dict[str, dict[str, Any]] = {}
        for key, value in kwargs.items():
            if key == "on_event":
                # 只在显式非 None 时写（None 是「未提供」而非「清空」）
                if value is not None:
                    spec.on_event = value
                continue
            mapping = _LEGACY_MAP.get(key)
            if mapping is None:
                unknown.append(key)
                continue
            group_name, field_name = mapping
            group_updates.setdefault(group_name, {})[field_name] = value

        if unknown:
            raise TypeError(
                f"AgentLoop() got unexpected keyword argument(s): "
                f"{sorted(unknown)}"
            )

        for group_name, updates in group_updates.items():
            cls_type = group_meta[group_name]
            current = groups[group_name]
            spec_dict = {f.name: getattr(current, f.name) for f in fields(current)}
            spec_dict.update(updates)
            object.__setattr__(spec, group_name, cls_type(**spec_dict))
        return spec

    def to_legacy_kwargs(self) -> dict[str, Any]:
        """展开回旧式 kwargs（供仍在用旧签名的内部代码/测试）。"""
        out: dict[str, Any] = {}
        for group_name in ("budget", "runtime", "capability", "context",
                           "permission", "scope"):
            group = getattr(self, group_name)
            for f in fields(group):
                legacy_name = _REVERSE_MAP.get((group_name, f.name))
                if legacy_name is not None:
                    out[legacy_name] = getattr(group, f.name)
        if self.on_event is not None:
            out["on_event"] = self.on_event
        return out


# ── 映射表（单一事实源）───────────────────────────────────────────────


def _build_legacy_map() -> dict[str, tuple[str, str]]:
    """旧参数名 → (组, 字段)。**与 ``AgentLoop.__init__`` 的旧签名一一对应**。"""
    return {
        # Budget
        "max_iterations": ("budget", "max_iterations"),
        "no_progress_window": ("budget", "no_progress_window"),
        "threshold_tokens": ("budget", "threshold_tokens"),
        "iteration_timeout_s": ("budget", "iteration_timeout_s"),
        "approval_timeout": ("budget", "approval_timeout"),
        "approval_on_timeout": ("budget", "approval_on_timeout"),
        "wrap_up_nudge": ("budget", "wrap_up_nudge"),
        "force_final_text": ("budget", "force_final_text"),
        # Runtime
        "stream_mode": ("runtime", "stream_mode"),
        "heartbeat_interval": ("runtime", "heartbeat_interval"),
        "trace_dir": ("runtime", "trace_dir"),
        "auto_git_commit": ("runtime", "auto_git_commit"),
        "readonly": ("runtime", "readonly"),
        # Capability
        "memory": ("capability", "memory"),
        "session_manager": ("capability", "session_manager"),
        "circuit_breaker": ("capability", "circuit_breaker"),
        "retry_policy": ("capability", "retry_policy"),
        "compact_config": ("capability", "compact_config"),
        "event_bus": ("capability", "event_bus"),
        "hooks": ("capability", "hooks"),
        "injectors": ("capability", "injectors"),
        # Context
        "system_prompt": ("context", "system_prompt"),
        "allowed_tools": ("context", "allowed_tools"),
        "strategy": ("context", "strategy"),
        "strategy_name": ("context", "strategy_name"),
        "enable_goal_injection": ("context", "enable_goal_injection"),
        "enable_memory_injection": ("context", "enable_memory_injection"),
        # 批次 V3-a：tier-1 tool_set 裁剪签名
        "task_signature": ("context", "task_signature"),
        "enable_hypothesis_auto_create": (
            "context", "enable_hypothesis_auto_create"
        ),
        "enable_claim_validation": ("context", "enable_claim_validation"),
        "strict_claim_validation": ("context", "strict_claim_validation"),
        "role": ("context", "role"),
        # Permission（**不经 __init__ 参数**：chat_loop 用 setattr 挂载，
        # 见 loop_engine_adapters/loop_tools 的 getattr 消费——因此不在此表，
        # 但 PermissionSpec 仍保留供显式 spec 传参）
        # Scope
        "workspace": ("scope", "workspace"),
        "strategy_dir": ("scope", "strategy_dir"),
        "runs_dir": ("scope", "runs_dir"),
        "results_tsv": ("scope", "results_tsv"),
        "write_roots": ("scope", "write_roots"),
        "read_roots": ("scope", "read_roots"),
        "session_id": ("scope", "session_id"),
    }


_LEGACY_MAP: dict[str, tuple[str, str]] = _build_legacy_map()
_REVERSE_MAP: dict[tuple[str, str], str] = {
    v: k for k, v in _LEGACY_MAP.items()
}
