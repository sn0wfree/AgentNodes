"""B1-a: node output contracts (``WorkflowNode.config["contract"]``).

Design: docs/design/crewai-design-borrow-20261116.md §3.

Scope of this file: the contract mechanism only. The sibling file
``test_workflow_node_contracts.py`` covers a different, older batch of
declared-but-unread knobs (``exec.node_max_retries`` /
``exec.node_timeout_seconds``) — not output shapes.
"""
from __future__ import annotations

import pytest

from agentnodes.kernel.swarm.types import AgentResult, AgentStatus
from agentnodes.kernel.workflow import node_types
from agentnodes.kernel.workflow.contract import (
    NodeContract,
    NodeContractError,
    enforce_contract,
    parse_contract,
    payload_of,
    validate_result,
)
from agentnodes.kernel.workflow.definition import WorkflowNode
from agentnodes.kernel.workflow.node_types import NodeContext


def _ctx(tmp_path, **overrides) -> NodeContext:
    kwargs = {
        "workspace": tmp_path,
        "strategy_name": "s",
        "objective": "o",
        "session_id": "sess",
        "upstream": {},
        "params": {},
        "llm_config": None,
        "loop_factory": None,
        "emit_event": None,
    }
    kwargs.update(overrides)
    return NodeContext(**kwargs)


def _result(artifacts=None, status=AgentStatus.SUCCESS) -> AgentResult:
    return AgentResult(
        agent_id="n",
        status=status,
        output="out",
        summary="sum",
        artifacts=artifacts or {},
    )


# ── parse_contract ───────────────────────────────────────────


def test_absent_contract_returns_none():
    assert parse_contract(None) is None
    assert parse_contract({}) is None
    assert parse_contract({"role": "researcher"}) is None


def test_parse_minimal_contract_defaults_to_fail():
    contract = parse_contract({"contract": {"required": ["verdict"]}})
    assert contract is not None
    assert contract.required == ("verdict",)
    assert contract.on_violation == "fail"
    assert contract.max_retries == 3


def test_parse_full_contract():
    contract = parse_contract({
        "contract": {
            "required": ["risk_passed", "risk_rating"],
            "schema": {
                "risk_passed": {"type": "bool"},
                "risk_rating": {"type": "str", "enum": ["Green", "Yellow", "Red"]},
            },
            "on_violation": "retry",
            "max_retries": 2,
        },
    })
    assert contract.required == ("risk_passed", "risk_rating")
    assert contract.on_violation == "retry"
    assert contract.max_retries == 2


@pytest.mark.parametrize("bad", [
    {"contract": "not a dict"},
    {"contract": {"required": "verdict"}},          # str, not list
    {"contract": {"required": [1]}},                # non-str entry
    {"contract": {"schema": ["x"]}},                # list, not dict
    {"contract": {"on_violation": "explode"}},      # not in the enum
    {"contract": {"max_retries": -1}},
    {"contract": {"max_retries": "3"}},
])
def test_malformed_contract_fails_loudly(bad):
    with pytest.raises(NodeContractError):
        parse_contract(bad)


# ── payload_of ───────────────────────────────────────────────


def test_payload_prefers_parsed_over_artifacts():
    result = _result({"parsed": {"verdict": "keep"}, "other": 1})
    assert payload_of(result) == {"verdict": "keep"}


def test_payload_falls_back_to_artifacts():
    result = _result({"verdict": "keep"})
    assert payload_of(result) == {"verdict": "keep"}


def test_payload_ignores_non_dict_parsed():
    result = _result({"parsed": ["not", "a", "dict"], "verdict": "keep"})
    assert payload_of(result) == {"parsed": ["not", "a", "dict"], "verdict": "keep"}


# ── validate_result ──────────────────────────────────────────


def test_validate_passes_on_matching_shape():
    contract = NodeContract(
        required=("risk_passed",),
        schema={
            "risk_passed": {"type": "bool"},
            "risk_rating": {"type": "str", "enum": ["Green", "Red"]},
        },
    )
    ok = _result({"parsed": {"risk_passed": True, "risk_rating": "Green"}})
    assert validate_result(ok, contract) == []


def test_validate_reports_missing_required():
    contract = NodeContract(required=("verdict",))
    violations = validate_result(_result({"parsed": {}}), contract)
    assert len(violations) == 1
    assert "verdict" in violations[0] and "missing" in violations[0]


def test_validate_optional_field_absent_is_fine():
    contract = NodeContract(schema={"topics": {"type": "array"}})
    assert validate_result(_result({"parsed": {}}), contract) == []


def test_validate_reports_wrong_type():
    contract = NodeContract(schema={"risk_passed": {"type": "bool"}})
    violations = validate_result(_result({"parsed": {"risk_passed": "yes"}}), contract)
    assert len(violations) == 1
    assert "bool" in violations[0] and "str" in violations[0]


def test_bool_is_not_accepted_as_int():
    # bool subclasses int in Python — the contract must not be fooled.
    contract = NodeContract(schema={"count": {"type": "int"}})
    assert validate_result(_result({"parsed": {"count": True}}), contract) != []


def test_validate_reports_enum_miss():
    contract = NodeContract(schema={"rating": {"type": "str", "enum": ["Green", "Red"]}})
    violations = validate_result(_result({"parsed": {"rating": "Blue"}}), contract)
    assert len(violations) == 1
    assert "Blue" in violations[0]


def test_validate_checks_array_item_types():
    contract = NodeContract(schema={"topics": {"type": "array", "items": {"type": "str"}}})
    violations = validate_result(
        _result({"parsed": {"topics": ["ok", 7]}}), contract,
    )
    assert len(violations) == 1
    assert "topics[1]" in violations[0]


def test_unsupported_schema_type_is_a_config_error():
    contract = NodeContract(schema={"x": {"type": "oneOf"}})
    with pytest.raises(NodeContractError):
        validate_result(_result({"parsed": {"x": 1}}), contract)


# ── enforce_contract ────────────────────────────────────────


def test_no_contract_passes_through_untouched(tmp_path):
    node = WorkflowNode(id="n", type="llm_agent")
    calls = {"n": 0}

    def run(ctx):
        calls["n"] += 1
        return _result({"anything": 1})

    result = enforce_contract(node, _ctx(tmp_path), run)
    assert calls["n"] == 1
    assert result.artifacts == {"anything": 1}
    assert result.status == AgentStatus.SUCCESS


def test_fail_turns_violation_into_error(tmp_path):
    node = WorkflowNode(
        id="n", type="llm_agent",
        config={"contract": {"required": ["verdict"]}},
    )
    result = enforce_contract(node, _ctx(tmp_path), lambda ctx: _result({"parsed": {}}))
    assert result.status == AgentStatus.ERROR
    assert "contract violation" in result.error
    assert "verdict" in result.error


def test_fail_error_is_retryable_by_the_outer_layer():
    """A bad product is transient, so it must not look like a config error."""
    from agentnodes.kernel.workflow.node_types import _retryable_node_error

    node = WorkflowNode(
        id="n", type="llm_agent",
        config={"contract": {"required": ["verdict"]}},
    )
    result = enforce_contract(node, _ctx(None), lambda ctx: _result({"parsed": {}}))
    assert _retryable_node_error(result.error) is True


def test_degrade_keeps_product_and_flags_it(tmp_path):
    node = WorkflowNode(
        id="n", type="llm_agent",
        config={"contract": {"required": ["verdict"], "on_violation": "degrade"}},
    )
    result = enforce_contract(node, _ctx(tmp_path), lambda ctx: _result({"parsed": {}}))
    assert result.status == AgentStatus.SUCCESS
    assert result.artifacts["_contract_violation"]
    assert "verdict" in result.artifacts["_contract_violation"][0]


def test_retry_feeds_violation_back_and_succeeds(tmp_path):
    node = WorkflowNode(
        id="n", type="llm_agent",
        config={"contract": {"required": ["verdict"], "on_violation": "retry"}},
    )
    seen_objectives: list[str] = []

    def run(ctx):
        seen_objectives.append(ctx.objective)
        if len(seen_objectives) == 1:
            return _result({"parsed": {}})
        return _result({"parsed": {"verdict": "keep"}})

    result = enforce_contract(node, _ctx(tmp_path), run)
    assert result.status == AgentStatus.SUCCESS
    assert len(seen_objectives) == 2
    assert "verdict" in seen_objectives[1]


def test_retry_gives_up_after_max_retries(tmp_path):
    node = WorkflowNode(
        id="n", type="llm_agent",
        config={"contract": {
            "required": ["verdict"], "on_violation": "retry", "max_retries": 2,
        }},
    )
    calls = {"n": 0}

    def run(ctx):
        calls["n"] += 1
        return _result({"parsed": {}})

    result = enforce_contract(node, _ctx(tmp_path), run)
    assert result.status == AgentStatus.ERROR
    assert calls["n"] == 3  # initial + 2 retries


def test_retry_on_deterministic_node_downgrades_to_fail(tmp_path):
    """python/tool nodes never read ``objective``; retrying is pointless."""
    node = WorkflowNode(
        id="n", type="tool",
        config={"contract": {"required": ["x"], "on_violation": "retry"}},
    )
    calls = {"n": 0}

    def run(ctx):
        calls["n"] += 1
        return _result({"parsed": {}})

    result = enforce_contract(node, _ctx(tmp_path), run)
    assert result.status == AgentStatus.ERROR
    assert calls["n"] == 1


def test_violation_emits_event(tmp_path):
    events: list[tuple[str, dict]] = []
    ctx = _ctx(tmp_path, emit_event=lambda t, d: events.append((t, d)))
    node = WorkflowNode(
        id="n", type="llm_agent",
        config={"contract": {"required": ["verdict"]}},
    )
    enforce_contract(node, ctx, lambda ctx_: _result({"parsed": {}}))
    assert events
    assert events[0][0] == "node.contract_violation"
    assert events[0][1]["node_id"] == "n"


def test_emit_failure_never_breaks_dispatch(tmp_path):
    def boom(t, d):
        raise RuntimeError("sink down")

    ctx = _ctx(tmp_path, emit_event=boom)
    node = WorkflowNode(
        id="n", type="llm_agent",
        config={"contract": {"required": ["verdict"], "on_violation": "degrade"}},
    )
    result = enforce_contract(node, ctx, lambda ctx_: _result({"parsed": {}}))
    assert result.status == AgentStatus.SUCCESS


# ── dispatch_node integration ───────────────────────────────


def test_dispatch_node_wires_the_contract(tmp_path):
    node = WorkflowNode(
        id="n", type="llm_agent",
        config={"role": "researcher", "contract": {"required": ["verdict"]}},
    )
    ctx = _ctx(tmp_path, loop_factory=lambda **kw: '{"no_verdict": 1}')
    result = node_types.dispatch_node(node, ctx)
    assert result.status == AgentStatus.ERROR
    assert "contract violation" in result.error


def test_dispatch_node_unchanged_without_contract(tmp_path):
    node = WorkflowNode(id="n", type="llm_agent", config={"role": "researcher"})
    ctx = _ctx(tmp_path, loop_factory=lambda **kw: '{"no_verdict": 1}')
    result = node_types.dispatch_node(node, ctx)
    assert result.status == AgentStatus.SUCCESS


def test_dispatch_node_rejects_unknown_type_before_contract(tmp_path):
    node = WorkflowNode(id="n", type="nope", config={"contract": {"required": ["x"]}})
    with pytest.raises(node_types.NodeDispatchError):
        node_types.dispatch_node(node, _ctx(tmp_path))
