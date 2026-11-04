"""BaseTool + ToolRegistry: tool infrastructure.

Paradigm v2: every tool carries a machine-collected brief (注册时从
docstring 首行 + execute 签名 + effects 生成) and a full docstring
(详细版说明书, 经 tool_help 按需读取). See docs/agent-tools-reference.md.
"""

from __future__ import annotations

import copy
import functools
import inspect
import json
import logging
import types
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    runtime_checkable,
)

logger = logging.getLogger(__name__)


# ── 副作用声明 (paradigm v2) ────────────────────────────────────────
# effects 集合声明工具会写什么: db / fs / net。readonly 过滤在 P3 迁移
# 后由 effects 派生; 迁移前保持 is_readonly 类属性。
EFFECT_DB = "db"
EFFECT_FS = "fs"
EFFECT_NET = "net"


# ── ToolGuard (DSH-inspired monotonic deny guard) ─────────────────


@runtime_checkable
class ToolGuard(Protocol):
    """Monotonic deny guard — can only reject, never force-allow.

    Guards are evaluated in registration order.  The first denial wins
    and no subsequent guard can override it.  This guarantees that
    security is monotonic — adding a guard can only restrict, never
    expand, the allowed tool set.
    """

    def check(self, name: str, params: Dict[str, Any]) -> Optional[str]:
        """Return a denial reason, or None to allow the tool call."""
        ...

_EFFECT_LABELS = {
    EFFECT_DB: "写DB",
    EFFECT_FS: "写FS",
    EFFECT_NET: "网络",
}

# 注入参数: 不出现在 LLM schema / 必填列表 (ToolContext 化后在 P2 落地,
# 此处先排除以免误入必填)。
_INJECTED_PARAMS = {"self", "ctx", "workspace", "session_id", "_progress_callback"}

# transient 错误: loop 对工具调用自动重试; 业务/容错失败不重试。
TRANSIENT_TOOL_ERRORS = (
    ValueError, TypeError, KeyError, ConnectionError, TimeoutError,
    OSError, IOError,
)


@dataclass
class ToolContext:
    """显式运行上下文 (paradigm v2):由 AgentLoop 构造并注入。

    LLM 不可见 (不在 schema 中); 工具经 execute 的 ``ctx`` 参数接收。
    """

    workspace: Optional[Path] = None
    session_id: Optional[str] = None
    # ── v2 path parameterization (study scenario) ──────────────────
    # Overrides for the default workspace/strategies/<name> layout.
    # Tools fall back to the legacy derivation when these are None.
    strategy_dir: Optional[Path] = None      # strategy.py/config.yaml source dir
    runs_dir: Optional[Path] = None          # run parent dir (results.tsv sibling)
    results_tsv: Optional[Path] = None       # results.tsv location
    write_roots: Optional[tuple[str, ...]] = None  # PathWhitelist write roots override
    read_roots: Optional[tuple[str, ...]] = None   # PathWhitelist read roots override
    emit_progress: Optional[Callable[[dict], None]] = None
    # Emit an SSE event to the frontend (wired to AgentLoop._emit).
    # Used by display tools (show_chart / show_report) to push
    # renderables into the chat stream + right panel.
    emit_event: Optional[Callable[[str, dict], None]] = None
    # Current assistant message id (injected by AgentLoop) — display
    # tools attach it to emitted events so the projector can persist
    # the chart/html part into the right assistant message.
    message_id: Optional[str] = None
    # ── Tier 1 A1: permission plumbing ─────────────────────────
    # Set by AgentLoop per-attempt. ``permission_evaluator`` is the
    # ruleset source of truth; ``permission_gateway`` is the async
    # handshake (ask → user response). ``tool_call_id`` keys
    # the request/response pairing in the gateway.
    permission_evaluator: Optional[Any] = None
    permission_gateway: Optional[Any] = None
    tool_call_id: Optional[str] = None
    # ── P0-2 D: capability seam injection ─────────────────────
    # Optional fields — set by AgentLoop when the tool needs them.
    # Tools consume them through ``tools_capability.get_data_store`` /
    # ``tools_capability.get_sandbox`` (helper module) which raise a
    # helpful error when the seam is missing. ``backtest_engine`` is
    # deferred to P0-3 once the BacktestEngine Protocol ships.
    data_store: Optional[Any] = None
    sandbox: Optional[Any] = None


class ToolError(Exception):
    """业务/容错失败: 确定性错误, 不重试, 由框架转 err_actionable 结构。"""

    def __init__(
        self,
        message: str,
        *,
        received: Any = None,
        expected: str = "",
        fix: str = "",
        tool: str = "",
        step: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = str(message)
        self.received = received
        self.expected = expected
        self.fix = fix
        self.tool = tool
        # 组合式工具（多步骤编排）标识失败环节, 如 'config_load' / 'data_gate' /
        # 'engine_run'（docs/run-backtest-data-gate.md）。
        self.step = step
        # 附加结构化字段（如数据就绪报告、workflow 指引）。
        self.extra = dict(extra) if extra else None

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"status": "error", "error": self.message}
        if self.received is not None:
            payload["received"] = _truncate_payload(self.received)
        if self.expected:
            payload["expected"] = self.expected
        if self.fix:
            payload["fix"] = self.fix
        if self.tool:
            payload["tool"] = self.tool
        if self.step:
            payload["step"] = self.step
        if self.extra:
            payload.update(self.extra)
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=False, default=str)


def _truncate_payload(value: Any, max_len: int = 200) -> Any:
    """Truncate error payloads keeping structure (same semantics as
    builtin_tools.utils.truncate)."""
    if isinstance(value, str):
        if len(value) > max_len:
            return value[:max_len] + f"... (truncated, total {len(value)} chars)"
        return value
    if isinstance(value, dict):
        return {k: _truncate_payload(v, max_len) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        items = [_truncate_payload(v, max_len) for v in value[:5]]
        if len(value) > 5:
            items.append(f"... (total {len(value)} items)")
        return items
    return value


def _doc_first_line(tool) -> str:
    """docstring 首行 = 简略版用途一句话 (与详细版同源)。

    Accepts a tool instance so instances with dynamic ``__doc__``
    (e.g. composite tools) produce their own brief.
    """
    doc = inspect.getdoc(tool)
    if doc:
        line = doc.strip().splitlines()[0].strip()
        if line:
            return line[:80]
    return ""


def _required_params(tool: BaseTool) -> List[str]:
    """execute 显式签名中无默认值的参数; **kwargs 存量工具回退 parameters.required。"""
    try:
        sig = inspect.signature(tool.execute)
    except (TypeError, ValueError, AttributeError):
        sig = None
    if sig is None or any(
        p.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
        for p in sig.parameters.values()
    ):
        try:
            return list(tool.parameters.get("required", []))
        except (AttributeError, TypeError):
            return []
    required = [
        name for name, p in sig.parameters.items()
        if name not in _INJECTED_PARAMS and p.default is p.empty
    ]
    if required:
        return required
    try:
        return list(tool.parameters.get("required", []))
    except (AttributeError, TypeError):
        return []


def _effects_label(tool: BaseTool) -> str:
    """effects 短标; 未声明时按 is_readonly 派生。"""
    if getattr(tool, "effects", None):
        labels = [_EFFECT_LABELS[e] for e in sorted(tool.effects) if e in _EFFECT_LABELS]
        if labels:
            return ",".join(labels)
    return "只读" if getattr(tool, "is_readonly", True) else "写"


def _build_tool_brief(tool: BaseTool) -> str:
    """注册时生成简略版目录条目。"""
    summary = _doc_first_line(tool)
    required = _required_params(tool)
    category = getattr(tool, "category", "other") or "other"
    parts = [f"- {tool.name}[{category}]: {summary}"]
    if required:
        parts.append(f"必填: {', '.join(required)}")
    parts.append(f"副作用: {_effects_label(tool)}")
    return "；".join(parts)


def _coerce_param_value(name: str, value: Any, annotation: Any) -> Any:
    """类型驱动容错: LLM 常见参数形状错误的统一矫正。

    - list[X]  ← str (JSON 字符串) → json.loads
    - list[X]  ← dict 单键包裹 ({"item": [...]}) → 解包
    - dict     ← str (JSON 字符串) → json.loads
    - int/float/bool ← str → 强转
    仅在声明类型与收到类型不匹配时触发; 失败抛 ToolError (结构化)。
    """
    if value is None:
        return value
    origin = get_origin(annotation)
    # Optional[X] / X | None → 取非 None 分支
    if origin is Union or origin is types.UnionType:
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            annotation = non_none[0]
            origin = get_origin(annotation)
    if origin is list:
        return _coerce_list(name, value, annotation)
    if origin is dict:
        return _coerce_dict(name, value)
    if annotation is int:
        return _coerce_scalar(name, value, "integer", int)
    if annotation is float:
        return _coerce_scalar(name, value, "number", float)
    if annotation is bool:
        return _coerce_bool(name, value)
    return value


def _coerce_list(name: str, value: Any, annotation: Any) -> Any:
    """Coerce a scalar/JSON-string/dict-wrapped value into a list."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            raise ToolError(
                f"invalid value for '{name}': not a valid JSON list",
                received=value,
                expected=f"list e.g. {_list_example(annotation)} or JSON string",
                fix=f"pass a list for '{name}'",
            )
        if isinstance(parsed, list):
            return parsed
    if isinstance(value, dict) and len(value) == 1:
        inner = next(iter(value.values()))
        if isinstance(inner, list):
            return inner
    return value


def _coerce_dict(name: str, value: Any) -> Any:
    """Coerce a JSON-string value into a dict."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            raise ToolError(
                f"invalid value for '{name}': not a valid JSON object",
                received=value,
                expected="object (JSON string or dict)",
                fix=f"pass an object for '{name}'",
            )
        if isinstance(parsed, dict):
            return parsed
    return value


def _coerce_scalar(name: str, value: Any, expected: str, caster) -> Any:
    """Coerce a string into a numeric scalar."""
    if isinstance(value, str):
        try:
            return caster(value)
        except ValueError:
            raise ToolError(
                f"invalid value for '{name}': expected {expected}",
                received=value,
                expected=expected,
            )
    return value


def _coerce_bool(name: str, value: Any) -> Any:
    """Coerce a string into a boolean."""
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in ("true", "1"):
            return True
        if lowered in ("false", "0"):
            return False
        raise ToolError(
            f"invalid value for '{name}': expected true/false",
            received=value,
            expected="boolean (true/false)",
        )
    return value


def _list_example(annotation: Any) -> str:
    inner = get_args(annotation)
    if inner:
        return f"[{inner[0].__name__ if hasattr(inner[0], '__name__') else inner[0]}]"
    return "[...]"


def _schema_from_signature(tool: BaseTool) -> Optional[Dict[str, Any]]:
    """显式签名 → JSON Schema (paradigm v2 单源)。

    - ctx/注入参数不进 schema
    - 注解: str/int/float/bool/list[X]/dict/X|None (单非 None 分支)
    - 无默认值参数 → required
    - **kwargs 存量工具或无可用注解 → None (回退手写 parameters)
    """
    try:
        sig = inspect.signature(tool.execute)
        hints = get_type_hints(tool.execute)
    except (TypeError, ValueError, NameError, AttributeError):
        return None
    if any(
        p.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
        for p in sig.parameters.values()
    ):
        return None
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for name, p in sig.parameters.items():
        if name in _INJECTED_PARAMS:
            continue
        annotation = hints.get(name, p.annotation)
        if annotation is p.empty or annotation is Any:
            continue
        js = _annotation_to_json_schema(annotation)
        if js is None:
            continue
        properties[name] = js
        if p.default is p.empty:
            required.append(name)
    if not properties:
        return None
    return {"type": "object", "properties": properties, "required": required}


def _annotation_to_json_schema(annotation: Any) -> Optional[Dict[str, Any]]:
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return _annotation_to_json_schema(non_none[0])
        return None
    if origin is list:
        inner = get_args(annotation)
        item = _annotation_to_json_schema(inner[0]) if inner else {}
        return {"type": "array", "items": item or {}}
    if origin is dict:
        return {"type": "object"}
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    return None


def make_strict_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively inject OpenAI strict-mode requirements.

    OpenAI's structured outputs (`strict: True`) require:
    - ``additionalProperties: false`` at every object level
    - All properties in the ``required`` array

    This helper walks the schema tree and adds these constraints.
    For dict-shaped fields (``additionalProperties: {schema}``), we
    **drop** the schema (strict mode doesn't allow arbitrary keys) —
    callers should switch to explicit schemas if strict mode is needed.

    Args:
        schema: JSON Schema dict.

    Returns:
        A new schema (deep-copied) suitable for OpenAI strict mode.
    """
    schema = copy.deepcopy(schema)
    _walk_strict(schema)
    return schema


def _walk_strict(node: Any) -> None:
    """Mutate ``node`` in place to add strict-mode constraints."""
    if not isinstance(node, dict):
        return
    # If additionalProperties is a schema (dict-shape), strict mode can't
    # express arbitrary keys — drop it. Tool authors should use an explicit
    # schema or list shape instead.
    if isinstance(node.get("additionalProperties"), dict):
        del node["additionalProperties"]
    # Object types must have additionalProperties: false
    if node.get("type") == "object":
        node["additionalProperties"] = False
        # All listed properties must be required
        props = node.get("properties")
        if isinstance(props, dict) and props:
            existing = node.get("required") or []
            if not isinstance(existing, list):
                existing = list(existing)
            # Union of explicit required + all properties
            node["required"] = sorted(set(existing) | set(props.keys()))
    # Walk children — properties
    for prop_name, prop_val in (node.get("properties") or {}).items():
        if isinstance(prop_val, dict):
            _walk_strict(prop_val)
    # items (array element schema)
    items = node.get("items")
    if isinstance(items, dict):
        _walk_strict(items)
    elif isinstance(items, list):
        for v in items:
            if isinstance(v, dict):
                _walk_strict(v)
    # additionalProperties (if it's a dict schema — already handled above)
    # allOf / anyOf / oneOf
    for key in ("allOf", "anyOf", "oneOf"):
        for sub in node.get(key, []) or []:
            if isinstance(sub, dict):
                _walk_strict(sub)


# ── tool_errors: execute 业务层标准化装饰器 ─────────────────────────
#
# 由 BaseTool.__init_subclass__ 自动应用于所有子类的 execute（零遗漏、
# 无需手动标注）。职责边界:
#   - 工具内部业务错误一律 raise ToolError(message, fix=, expected=, step=)
#     → 自动注入 tool 名 → to_json（确定性、不重试）
#   - 非 transient 意外异常 → 结构化兜底（带 tool 名 + step 名）
#   - 返回值统一: str 原样返回; dict → JSON; 其他 → 包装
#   - transient 异常 (ValueError/TypeError/...) → re-raise 交给 invoke/loop 重试
# invoke 保持框架层（参数 coerce / 权限 / transient 重试）不动。
# docs/run-backtest-data-gate.md


def tool_errors(func: Callable) -> Callable:
    """Standardize a tool ``execute``: ToolError → JSON, dict → JSON,
    non-transient exceptions → structured error JSON.

    Not meant to be applied manually — ``BaseTool.__init_subclass__``
    wraps every subclass ``execute`` automatically.
    """

    @functools.wraps(func)
    def wrapper(self, *args: Any, **kwargs: Any) -> str:
        try:
            result = func(self, *args, **kwargs)
        except ToolError as exc:
            if not exc.tool:
                exc.tool = getattr(self, "name", "")
            if not exc.step:
                exc.step = getattr(self, "name", "")
            return exc.to_json()
        except TRANSIENT_TOOL_ERRORS:
            raise
        except Exception as exc:                    # noqa: BLE001
            logger.exception("tool %s execute raised", getattr(self, "name", "?"))
            return json.dumps({
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "tool": getattr(self, "name", ""),
                "step": getattr(self, "name", ""),
            }, ensure_ascii=False)
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False, default=str)
        return json.dumps(
            {"status": "ok", "result": result}, ensure_ascii=False, default=str
        )

    wrapper._tool_errors_wrapped = True  # type: ignore[attr-defined]
    return wrapper


class BaseTool(ABC):
    """Tool base class.

    Attributes:
        name: Unique tool identifier.
        description: Tool description shown to the LLM.
        parameters: Parameter definition in JSON Schema format.
        repeatable: Whether the tool may be called more than once.
        strict: If True, schema is converted to OpenAI strict-mode
            (structured outputs) before sending to the LLM.
    """

    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {}
    repeatable: bool = False
    strict: bool = False
    # ── paradigm v2 ─────────────────────────────────────────────
    category: str = "other"       # 领域分类: 文件/回测/因子/行情/分析/技能/Web/Goal/Shell
    effects: frozenset[str] = frozenset()  # {EFFECT_DB, EFFECT_FS, EFFECT_NET}
    brief: str = ""               # 注册时由 ToolRegistry 自动填充

    def __init_subclass__(cls, **kw: Any) -> None:
        """Automatically wrap every subclass ``execute`` with ``tool_errors``.

        Zero-effort standardized error output for ALL tools (and for the
        decomposed step classes that inherit BaseTool without being
        registered). Idempotent: a subclass re-declaring an already
        wrapped ``execute`` is not double-wrapped.
        """
        super().__init_subclass__(**kw)
        exec_fn = cls.__dict__.get("execute")
        if exec_fn is not None and not getattr(exec_fn, "_tool_errors_wrapped", False):
            cls.execute = tool_errors(exec_fn)

    @property
    def is_readonly(self) -> bool:
        """readonly 由 effects 派生 (v2); 子类声明的 is_readonly 类属性优先。"""
        return not self.effects

    @classmethod
    def check_available(cls) -> bool:
        """Check if this tool's dependencies are met.

        Override in subclasses to check for API keys, packages, etc.
        Tools that return False are excluded from the registry.
        """
        return True

    @abstractmethod
    def execute(self, **kwargs: Any) -> str:
        """Execute the tool and return a JSON string.

        v2: 显式签名 + 注解 (``def execute(self, ctx, strategy_name: str)``)
        提供类型/默认值单源; ctx 由框架注入 (ToolContext)。
        """

    # ── v2 统一入口: 容错 → execute → 意外异常结构化兜底 ─────────

    def invoke(self, kwargs: Dict[str, Any]) -> str:
        """Framework entry point: coerce params, run execute, catch surprises.

        - 容错失败 (ToolError) → 确定性错误 JSON (不重试)
        - transient 异常 → re-raise (loop 负责重试)
        - 其他意外异常 → 结构化 err JSON (带 tool 名, 消灭无结构输出)
        - ``ToolResult`` → A3 wire envelope (data + views)
        """
        try:
            cleaned = self._coerce_params(kwargs)
            raw = self._dispatch(cleaned)
        except ToolError as exc:
            return exc.to_json()
        except TRANSIENT_TOOL_ERRORS:
            raise
        except Exception as exc:                    # noqa: BLE001
            logger.exception("tool %s raised", self.name)
            return json.dumps({
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "tool": self.name,
            }, ensure_ascii=False)

        # A3: structured ToolResult → wire envelope; legacy str/dict pass through.
        from .tool_result import ToolResult, to_json

        if isinstance(raw, ToolResult):
            return to_json(raw)
        return raw

    def _dispatch(self, cleaned: Dict[str, Any]) -> str:
        """Call ``execute`` with the right signature. v2 tools take
        ``(self, ctx, **kwargs)``; legacy tools take ``(self, **kwargs)``.
        Detected once via inspect — no runtime overhead after first call.
        """
        import inspect as _inspect
        try:
            sig = _inspect.signature(self.execute)
            takes_ctx = any(
                p.name == "ctx" or p.kind is _inspect.Parameter.VAR_KEYWORD
                for p in sig.parameters.values()
            )
        except (TypeError, ValueError):
            takes_ctx = False
        if takes_ctx:
            ctx = cleaned.pop("ctx", None)
            return self.execute(ctx=ctx, **cleaned)
        return self.execute(**cleaned)

    async def ainvoke(
        self,
        kwargs: Dict[str, Any],
        ctx: Optional["ToolContext"] = None,
    ) -> str:
        """Async entry point used by AgentLoop. Wraps ``invoke`` with
        the Tier 1 A1 permission gate: ask -> SSE -> user response.

        ``ctx`` is optional — when absent (synchronous fallback or
        pre-permission plumbing) the tool runs without a permission
        check, matching the legacy ``invoke`` behavior. The agent
        loop always passes ``ctx`` for every tool invocation.
        """
        if ctx is not None and ctx.permission_evaluator is not None:
            await self._enforce_permission(kwargs, ctx)
        return self.invoke(kwargs)

    async def _enforce_permission(
        self,
        kwargs: Dict[str, Any],
        ctx: "ToolContext",
    ) -> None:
        """Evaluate the ruleset; honor allow / ask / deny."""
        # Local imports avoid a circular dependency at module load
        # (permission -> tools -> permission).
        from strategy_research.kernel.permission import (
            PermissionAction,
            PermissionDeniedError,
            PermissionGateway,
        )

        evaluator = ctx.permission_evaluator
        if evaluator is None:
            return
        decision = evaluator.evaluate(self.name, kwargs)
        if decision.action == PermissionAction.ALLOW:
            return
        if decision.action == PermissionAction.DENY:
            err = PermissionDeniedError(
                f"Permission denied: {self.name} pattern={decision.pattern}",
                rule=decision.rule,
                target=decision.target,
            )
            raise err

        # ASK — defer to the user via the gateway.
        gateway: PermissionGateway | None = ctx.permission_gateway
        if gateway is None:
            # No gateway wired up (e.g. tests, sync path). Fail open
            # to ASK behaviour but skip the handshake — the loop
            # logs a warning so operators notice.
            logger.warning(
                "tool %s gated by ASK but no permission_gateway on ctx; "
                "treating as allow", self.name,
            )
            return

        tool_call_id = ctx.tool_call_id or ""
        if not tool_call_id:
            logger.warning(
                "tool %s ASK without tool_call_id — failing allow",
                self.name,
            )
            return

        # Stash session_id on ctx for the SSE hook — the gateway's
        # ``on_request`` callback runs without any reference to ctx,
        # so we thread session_id through the gateway's request
        # by piggybacking it on the args payload the hook receives.
        scoped_args = dict(kwargs)
        scoped_args["__session_id__"] = ctx.session_id or ""

        response = await gateway.request(
            tool_call_id=tool_call_id,
            tool_name=self.name,
            args=scoped_args,
            decision=decision,
        )

        if response.action == PermissionAction.DENY:
            err = PermissionDeniedError(
                response.reason or f"User rejected: {self.name}",
                rule=decision.rule,
                target=decision.target,
            )
            raise err
        # ALLOW (one-shot or permanent) — gateway already persisted
        # the rule if permanent=True.

    def enforce_permission_sync(self, kwargs: Dict[str, Any], ctx: Any) -> None:
        """Synchronous permission gate for sync executors (B1-d).

        ``react`` is a synchronous node, so it cannot await
        ``ainvoke``. This applies the *rule* verdict synchronously —
        DENY raises, ALLOW passes — and for ASK it falls back to the
        documented fail-open path (no gateway handshake is possible
        without a loop). That exactly matches the existing
        ``ASK + no gateway`` semantics in ``_enforce_permission``
        (``tools.py:684-692``), so chat behaviour is unchanged for
        rule-allowed tools and SAFE for rule-denied ones.

        Async callers (AgentLoop) keep using ``ainvoke`` and get the
        full ASK handshake.
        """
        from strategy_research.kernel.permission import PermissionAction, PermissionDeniedError

        evaluator = getattr(ctx, "permission_evaluator", None)
        if evaluator is None:
            return
        decision = evaluator.evaluate(self.name, kwargs)
        if decision.action == PermissionAction.ALLOW:
            return
        if decision.action == PermissionAction.DENY:
            raise PermissionDeniedError(
                f"Permission denied: {self.name} pattern={decision.pattern}",
                rule=decision.rule,
                target=decision.target,
            )
        # ASK: no async handshake available on this path.
        gateway = getattr(ctx, "permission_gateway", None)
        if gateway is None:
            logger.warning(
                "tool %s gated by ASK but no permission_gateway on ctx; "
                "treating as allow", self.name,
            )
            return
        # A gateway exists but we cannot await it synchronously. Deny
        # rather than silently allowing: a configured gateway means the
        # operator expects ASK to be honoured.
        raise PermissionDeniedError(
            f"Permission ASK for {self.name} requires an async executor; "
            f"denied on the synchronous path",
            rule=decision.rule,
            target=decision.target,
        )

    def _coerce_params(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """框架统一容错: 按 execute 显式签名注解, 仅在类型不匹配时触发。

        **kwargs 存量工具 (无注解) 原样返回 — P3 迁移后自动获得容错。
        """
        try:
            sig = inspect.signature(self.execute)
            # 注解可能是字符串 (from __future__ import annotations)
            hints = get_type_hints(self.execute)
        except (TypeError, ValueError, NameError):
            return dict(kwargs)
        if any(
            p.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
            for p in sig.parameters.values()
        ):
            return dict(kwargs)
        out = dict(kwargs)
        # 显式签名工具: 以签名为白名单 — 注入参数 (workspace/session_id/
        # _progress_callback) 由 ctx 承载, 不再透传 (P3 迁移后存量工具的
        # 注入依赖随之移除); ctx 保留。
        sig_names = set(sig.parameters)
        for key in list(out):
            if key not in sig_names and key != "ctx":
                out.pop(key, None)
        for name, p in sig.parameters.items():
            if name in _INJECTED_PARAMS or name not in out:
                continue
            annotation = hints.get(name, p.annotation)
            if annotation is p.empty or annotation is Any:
                continue
            out[name] = _coerce_param_value(name, out[name], annotation)
        return out

    def to_openai_schema(self) -> Dict[str, Any]:
        """Convert to OpenAI function calling format.

        v2: parameters derive from the execute signature (annotations +
        defaults) when available; legacy **kwargs tools fall back to the
        hand-written `parameters` dict until migrated.

        When ``strict=True``, the schema is rewritten to comply with
        OpenAI's structured outputs requirements and a ``strict`` flag
        is added so the provider enforces it server-side.
        """
        params = _schema_from_signature(self)
        if params is None:
            params = self.parameters or {"type": "object", "properties": {}, "required": []}
        if self.strict:
            params = make_strict_schema(params)
        fn_def: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "parameters": params,
        }
        if self.strict:
            fn_def["strict"] = True
        return {
            "type": "function",
            "function": fn_def,
        }


class ToolRegistry:
    """Tool registry with DSH-inspired scoped tools and guard pipeline."""

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}
        self._guards: List[ToolGuard] = []
        self._denied: set = set()

    def register(self, tool: BaseTool) -> None:
        """Register a tool (collects its brief at registration time)."""
        self._tools[tool.name] = tool
        tool.brief = _build_tool_brief(tool)

    def get(self, name: str) -> Optional[BaseTool]:
        """Retrieve a tool by name."""
        return self._tools.get(name)

    def all_tools(self) -> List[BaseTool]:
        """All registered tools (stable order)."""
        return list(self._tools.values())

    def resolve_harness_for(self, task_signature: str) -> List[str]:
        """装配期问一次：该 task_signature 下允许的工具名（施工方案 §4.7）。

        **只做减法**：返回已注册工具与生效 tool_set 的交集。RSI 不能凭空
        启用未注册的工具，否则等于绕过权限闸。未启用 RSI 时返回全部工具，
        行为与改动前完全一致。
        """
        names = list(self._tools)
        try:
            from strategy_research.kernel.agent.harness_resolver import (
                resolve_harness,
            )

            cfg = resolve_harness(task_signature)
            if cfg.tool_set is None:
                return names
            allowed = set(cfg.tool_set)
            return [n for n in names if n in allowed]
        except Exception:  # noqa: BLE001 —— 装配期不得因 RSI 崩溃
            return names

    def get_definitions(self) -> List[Dict[str, Any]]:
        """Return all tools in OpenAI function calling format."""
        return [t.to_openai_schema() for t in self._tools.values()]

    def execute(
        self, name: str, params: Dict[str, Any], *, ctx: Any = None,
    ) -> str:
        """Execute a tool and guarantee a valid JSON return value.

        v2: routes through tool.invoke() so coercion + error fallback
        apply uniformly (composite tools get the same guarantees).

        B1-d: ``ctx`` (a ``ToolContext``) is optional. When provided the
        permission ruleset is enforced synchronously before invocation —
        DENY raises, ASK without an obtainable answer is refused. Callers
        that pass no ctx keep the legacy unprotected behaviour (tests,
        script paths).
        """
        tool = self._tools.get(name)
        if not tool:
            return json.dumps({"status": "error", "error": f"Tool '{name}' not found"}, ensure_ascii=False)
        try:
            if ctx is not None:
                tool.enforce_permission_sync(dict(params), ctx)
            invoke_kwargs = dict(params)
            if ctx is not None:
                invoke_kwargs["ctx"] = ctx
            return tool.invoke(invoke_kwargs)
        except Exception as exc:
            from strategy_research.kernel.permission import PermissionDeniedError

            if isinstance(exc, PermissionDeniedError):
                # Denied tools return a structured refusal, not an
                # exception — the model should see why and adapt.
                return json.dumps({
                    "status": "error",
                    "tool": name,
                    "error": str(exc),
                    "permission_denied": True,
                }, ensure_ascii=False)
            logger.exception("Tool %s failed", name)
            return json.dumps({
                "status": "error", "tool": name,
                "error": str(exc),
            }, ensure_ascii=False)

    # ── DSH-inspired: scoped tools + guard pipeline ──────────────

    def restrict(
        self,
        *,
        deny: Optional[List[str]] = None,
        allow: Optional[List[str]] = None,
    ) -> None:
        """Scope tools: deny removes tools, allow keeps only those listed.

        ``deny`` is cumulative — calling restrict multiple times adds to
        the denied set.  ``allow`` replaces the allowed set (last call wins).

        Typical use: child agent inherits parent's tools but restricts
        to a safe subset::

            child_registry = parent_registry.restricted(
                deny=["task", "shell"],
            )
        """
        if deny:
            self._denied.update(deny)
            for name in deny:
                self._tools.pop(name, None)
        if allow:
            allowed_set = set(allow)
            self._tools = {k: v for k, v in self._tools.items() if k in allowed_set}

    def restricted(
        self,
        *,
        deny: Optional[List[str]] = None,
        allow: Optional[List[str]] = None,
    ) -> "ToolRegistry":
        """Return a new registry with restrictions applied (immutable variant)."""
        import copy
        new = copy.copy(self)
        new._tools = dict(self._tools)
        new._guards = list(self._guards)
        new._denied = set(self._denied)
        new.restrict(deny=deny, allow=allow)
        return new

    def guard(self, guard: ToolGuard) -> None:
        """Register a monotonic deny guard.

        Guards are evaluated in registration order before tool execution.
        The first denial wins — no subsequent guard can override it.
        """
        self._guards.append(guard)

    def check_guards(self, name: str, params: Dict[str, Any]) -> Optional[str]:
        """Run all guards; return denial reason or None to allow.

        Denied tools are also checked against the denied set.
        """
        if name in self._denied:
            return f"Tool '{name}' is denied by restriction"
        for g in self._guards:
            try:
                reason = g.check(name, params)
                if reason:
                    return reason
            except Exception:  # noqa: BLE001
                logger.debug("Guard %s raised, skipping", type(g).__name__)
        return None

    @property
    def tool_names(self) -> List[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
