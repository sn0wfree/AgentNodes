"""Node output contract — declare and validate what a node must produce.

Design: docs/design/crewai-design-borrow-20261116.md §3 (B1).

Why this file exists: ``_dispatch_*`` handlers return a free-form
``AgentResult(summary, artifacts, metrics)`` whose shape is decided by the
prompt alone. Nothing between "tool executed" and "statistics layer"
(``claim_validator`` / ``validation_gate``) constrains the product, so
declared-but-unconsumed verdicts (``critic``, ``study_reviewer``,
``risk_controller``) have nowhere to be checked and ``SwarmPreset.branches``
has nothing trustworthy to branch on.

The contract lives in ``WorkflowNode.config["contract"]`` — ``config`` is
already a ``dict[str, Any]`` passthrough (``definition.py:71``), so adding
it needs **zero schema change**.

Deliberately narrow: validate STRUCTURE only (field present / type / enum /
array item type). Semantics stay in the prompt. Supporting the full JSON
Schema would make this a second prompt and invite drift (design R1).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable

from agentnodes.kernel.swarm.types import AgentResult, AgentStatus

CONTRACT_KEY = "contract"

_ON_VIOLATION_VALUES = ("fail", "retry", "degrade")
_DEFAULT_MAX_RETRIES = 3

# ``retry`` hands the violation back by rewriting ``ctx.objective``, so it
# only means anything for the LLM-backed dispatchers (``_dispatch_python`` /
# ``_dispatch_tool`` never read ``objective`` — a blind retry there would
# re-run the exact same deterministic call). Non-LLM nodes downgrade to
# ``fail`` rather than burn attempts that cannot change the outcome.
_RETRY_CAPABLE_TYPES = ("llm_agent", "planner", "evaluator")


class NodeContractError(ValueError):
    """Malformed contract configuration (a config-level, non-retryable error)."""


@dataclass(frozen=True)
class NodeContract:
    """Declared shape of a node's product.

    ``required`` — field names that must be present.
    ``schema``   — per-field rules; JSON-Schema subset (type/enum/items).
    ``on_violation`` — ``fail`` (default) | ``retry`` | ``degrade``.
    ``max_retries``  — only consulted when ``on_violation == "retry"``.
    """

    required: tuple[str, ...] = ()
    schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    on_violation: str = "fail"
    max_retries: int = _DEFAULT_MAX_RETRIES


# ── Parsing ────────────────────────────────────────────────────


def parse_contract(config: dict[str, Any] | None) -> NodeContract | None:
    """Build a NodeContract from a node's ``config``, or None when absent.

    Raises NodeContractError for a malformed declaration: an opt-in field
    that is silently ignored is worse than one that fails loudly.
    """
    if not config:
        return None
    raw = config.get(CONTRACT_KEY)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise NodeContractError(
            f"config.contract must be a dict, got {type(raw).__name__}"
        )

    required = raw.get("required") or ()
    if not isinstance(required, (list, tuple)):
        raise NodeContractError(
            f"config.contract.required must be a list, got {type(required).__name__}"
        )
    for name in required:
        if not isinstance(name, str):
            raise NodeContractError(
                f"config.contract.required entries must be str, got {type(name).__name__}"
            )

    schema = raw.get("schema") or {}
    if not isinstance(schema, dict):
        raise NodeContractError(
            f"config.contract.schema must be a dict, got {type(schema).__name__}"
        )

    on_violation = raw.get("on_violation", "fail")
    if on_violation not in _ON_VIOLATION_VALUES:
        raise NodeContractError(
            f"config.contract.on_violation must be one of "
            f"{_ON_VIOLATION_VALUES}, got {on_violation!r}"
        )

    max_retries = raw.get("max_retries", _DEFAULT_MAX_RETRIES)
    if not isinstance(max_retries, int) or isinstance(max_retries, bool):
        raise NodeContractError(
            f"config.contract.max_retries must be int, got {type(max_retries).__name__}"
        )
    if max_retries < 0:
        raise NodeContractError(
            f"config.contract.max_retries must be >= 0, got {max_retries}"
        )

    return NodeContract(
        required=tuple(required),
        schema=dict(schema),
        on_violation=on_violation,
        max_retries=max_retries,
    )


# ── Validation ─────────────────────────────────────────────────


def _type_ok(value: Any, expected: str) -> bool:
    if expected in ("any", ""):
        return True
    # bool is a subclass of int in Python; check it first so True isn't
    # accepted as an int field.
    if expected == "bool":
        return isinstance(value, bool)
    if expected == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "float":
        return isinstance(value, float)
    if expected in ("number", "num"):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected in ("str", "string"):
        return isinstance(value, str)
    if expected in ("list", "array"):
        return isinstance(value, (list, tuple))
    if expected in ("dict", "object"):
        return isinstance(value, dict)
    raise NodeContractError(f"unsupported schema type {expected!r}")


def _field_violations(name: str, value: Any, rule: dict[str, Any]) -> list[str]:
    """Check one field against its rule; return human-readable violations."""
    out: list[str] = []
    expected = rule.get("type")
    if expected and not _type_ok(value, expected):
        out.append(f"{name}: expected {expected}, got {type(value).__name__}")

    enum = rule.get("enum")
    if enum is not None and value not in enum:
        out.append(f"{name}: {value!r} not in {list(enum)!r}")

    items = rule.get("items")
    if items and isinstance(value, (list, tuple)):
        item_type = items.get("type") if isinstance(items, dict) else None
        if item_type:
            for idx, item in enumerate(value):
                if not _type_ok(item, item_type):
                    out.append(
                        f"{name}[{idx}]: expected {item_type}, "
                        f"got {type(item).__name__}"
                    )
    return out


def payload_of(result: AgentResult) -> dict[str, Any]:
    """The dict a contract validates against.

    ``_dispatch_llm_agent`` puts a successful ``json.loads`` under
    ``artifacts["parsed"]``; python/tool nodes leave structured data directly
    in ``artifacts``. Prefer the parsed dict, fall back to artifacts so both
    shapes are covered without reparsing (design R5).
    """
    artifacts = result.artifacts or {}
    parsed = artifacts.get("parsed")
    if isinstance(parsed, dict):
        return parsed
    return artifacts


def validate_result(result: AgentResult, contract: NodeContract) -> list[str]:
    """Return violations of ``contract`` by ``result``; empty means valid."""
    payload = payload_of(result)

    violations: list[str] = []
    for name in contract.required:
        if name not in payload:
            violations.append(f"{name}: missing required field")

    for name, rule in contract.schema.items():
        if name not in payload:
            # Absence is reported by the ``required`` loop above; a schema
            # entry alone describes an optional field.
            continue
        violations.extend(_field_violations(name, payload[name], rule))

    return violations


# ── Enforcement ────────────────────────────────────────────────


def _violation_summary(violations: list[str]) -> str:
    return "；".join(violations)


def _emit_violation(
    ctx: Any,
    node: Any,
    contract: NodeContract,
    violations: list[str],
    attempt: int,
) -> None:
    emit = getattr(ctx, "emit_event", None)
    if emit is None:
        return
    try:
        emit(
            "node.contract_violation",
            {
                "node_id": getattr(node, "id", ""),
                "attempt": attempt,
                "on_violation": contract.on_violation,
                "violations": violations,
            },
        )
    except Exception:  # noqa: BLE001 — observation must never break dispatch
        pass


def _failed(node: Any, result: AgentResult, violations: list[str]) -> AgentResult:
    """Turn a contract violation into a node ERROR envelope.

    ``AgentStatus.ERROR`` (not SUCCESS-with-flag) so that
    ``_retryable_node_error`` and the executor's retry layer see it; the
    message intentionally avoids the permanent-error markers so a transient
    bad product can be retried by the outer loop.
    """
    return AgentResult(
        agent_id=getattr(node, "id", result.agent_id),
        status=AgentStatus.ERROR,
        output=result.summary or result.output,
        error=f"contract violation: {_violation_summary(violations)}",
        elapsed_s=result.elapsed_s,
        summary=result.summary,
        artifacts=dict(result.artifacts or {}),
        metrics=dict(result.metrics or {}),
    )


def enforce_contract(
    node: Any,
    ctx: Any,
    run: Callable[[Any], AgentResult],
) -> AgentResult:
    """Run the node and enforce its declared contract, if any.

    ``run`` receives a (possibly rewritten) NodeContext so ``retry`` can hand
    the violation back as a corrective instruction. Nodes without a
    ``contract`` key pass through untouched — zero behaviour change.
    """
    contract = parse_contract(getattr(node, "config", None))
    if contract is None:
        return run(ctx)

    result = run(ctx)
    violations = validate_result(result, contract)
    if not violations:
        return result

    _emit_violation(ctx, node, contract, violations, attempt=0)

    if contract.on_violation == "degrade":
        # Keep the product, but make the shortfall loud downstream.
        result.artifacts["_contract_violation"] = violations
        return result

    if contract.on_violation == "retry" and node.type not in _RETRY_CAPABLE_TYPES:
        # Deterministic nodes cannot be fixed by re-running them.
        return _failed(node, result, violations)

    if contract.on_violation == "retry":
        feedback = _violation_summary(violations)
        for attempt in range(1, contract.max_retries + 1):
            retry_ctx = replace(
                ctx,
                objective=(
                    f"{ctx.objective}\n\n"
                    f"## 上次产出不合规，请修正后重做\n{feedback}"
                ).strip(),
            )
            result = run(retry_ctx)
            violations = validate_result(result, contract)
            if not violations:
                return result
            _emit_violation(ctx, node, contract, violations, attempt=attempt)
        return _failed(node, result, violations)

    # on_violation == "fail"
    return _failed(node, result, violations)


__all__ = [
    "CONTRACT_KEY",
    "NodeContract",
    "NodeContractError",
    "enforce_contract",
    "parse_contract",
    "payload_of",
    "validate_result",
]
