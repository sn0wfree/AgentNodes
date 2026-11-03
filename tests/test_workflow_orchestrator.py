"""Tests for the DAG orchestration chat: submit_dag_step tool + drafts.

Covers:
- SubmitDagStepTool validation matrix (valid / bad type / singletons /
  cycle / edge refs / required config / id format / node cap).
- Draft persistence endpoints (PUT/GET/DELETE upsert semantics).
- Orchestrator session endpoint (idempotent dag:{name} session).
"""

from __future__ import annotations

import json

import pytest

from strategy_research.kernel.workflow.orchestrate_tool import SubmitDagStepTool


def _dag(nodes: list[dict], edges: list[dict]) -> dict:
    return {"nodes": nodes, "edges": edges}


def _submit(dag: dict) -> dict:
    tool = SubmitDagStepTool()
    return json.loads(tool.execute(None, dag=dag))


# ── submit_dag_step tool ─────────────────────────────────────────────


def test_valid_dag_applied():
    r = _submit(_dag(
        nodes=[
            {"id": "a", "type": "llm_agent", "label": "A", "config": {"role": "researcher"}},
            {"id": "b", "type": "tool", "label": "B", "config": {"tool": "x"}},
            {"id": "c", "type": "evaluator", "label": "C", "config": {}},
        ],
        edges=[{"source": "a", "target": "b"}, {"source": "b", "target": "c"}],
    ))
    assert r["applied"] is True
    assert r["nodes"] == 3 and r["edges"] == 2


def test_unknown_type_rejected():
    r = _submit(_dag(
        nodes=[{"id": "a", "type": "chatbot", "label": "A", "config": {}}],
        edges=[],
    ))
    assert r["applied"] is False
    assert any("unknown type 'chatbot'" in e for e in r["errors"])


def test_singleton_types_max_one():
    r = _submit(_dag(
        nodes=[
            {"id": "a", "type": "approval", "label": "A", "config": {}},
            {"id": "b", "type": "approval", "label": "B", "config": {}},
        ],
        edges=[{"source": "a", "target": "b"}],
    ))
    assert r["applied"] is False
    assert any("may appear at most once" in e for e in r["errors"])


def test_cycle_rejected():
    r = _submit(_dag(
        nodes=[
            {"id": "a", "type": "llm_agent", "label": "A", "config": {"role": "r"}},
            {"id": "b", "type": "llm_agent", "label": "B", "config": {"role": "r"}},
        ],
        edges=[{"source": "a", "target": "b"}, {"source": "b", "target": "a"}],
    ))
    assert r["applied"] is False
    assert any("cycle" in e.lower() for e in r["errors"])


def test_missing_edge_target_rejected():
    r = _submit(_dag(
        nodes=[{"id": "a", "type": "llm_agent", "label": "A", "config": {"role": "r"}}],
        edges=[{"source": "a", "target": "ghost"}],
    ))
    assert r["applied"] is False
    assert any("target 'ghost' not found" in e for e in r["errors"])


def test_required_config_enforced():
    r = _submit(_dag(
        nodes=[{"id": "a", "type": "llm_agent", "label": "A", "config": {}}],
        edges=[],
    ))
    assert r["applied"] is False
    assert any("missing required config 'role'" in e for e in r["errors"])


def test_bad_id_rejected():
    r = _submit(_dag(
        nodes=[{"id": "bad id!", "type": "llm_agent", "label": "A", "config": {"role": "r"}}],
        edges=[],
    ))
    assert r["applied"] is False
    assert any("must match" in e for e in r["errors"])


def test_node_count_cap():
    nodes = [
        {"id": f"n{i}", "type": "llm_agent", "label": f"N{i}", "config": {"role": "r"}}
        for i in range(51)
    ]
    r = _submit(_dag(nodes=nodes, edges=[]))
    assert r["applied"] is False
    assert any("exceeds limit 50" in e for e in r["errors"])


def test_malformed_dag_param():
    tool = SubmitDagStepTool()
    r = json.loads(tool.execute(None, dag="not-a-dict"))
    assert r["applied"] is False
    assert r["errors"]


# ── structural robustness (LLM serializes nested values as strings) ──


def test_string_node_element_readable_error():
    """A bare-string nodes[] element must yield a readable error, not a
    raw AttributeError ('str' object has no attribute 'get')."""
    r = _submit(_dag(nodes=["check", "diagnose"], edges=[]))
    assert r["applied"] is False
    assert any("nodes[0] 必须是对象" in e for e in r["errors"])
    assert "AttributeError" not in json.dumps(r)


def test_string_edge_element_readable_error():
    r = _submit(_dag(
        nodes=[{"id": "a", "type": "tool", "label": "A", "config": {"tool": "x"}}],
        edges=["a->b"],
    ))
    assert r["applied"] is False
    assert any("edges[0] 必须是对象" in e for e in r["errors"])


def test_string_config_normalized_to_dict():
    """config serialized as a JSON string is salvaged, then validated."""
    r = _submit(_dag(
        nodes=[
            {"id": "check", "type": "tool", "label": "数据检查",
             "config": '{"tool": "check_data", "params": {"cols": ["close"]}}'},
        ],
        edges=[],
    ))
    assert r["applied"] is True


def test_json_string_node_salvaged():
    r = _submit(_dag(
        nodes=[
            '{"id": "approve", "type": "approval", "label": "人工审阅", '
            '"config": {"message": "审阅诊断结论"}}',
        ],
        edges=[],
    ))
    assert r["applied"] is True


def test_invalid_string_config_readable_error():
    r = _submit(_dag(
        nodes=[{"id": "a", "type": "tool", "label": "A", "config": "not-json{"}],
        edges=[],
    ))
    assert r["applied"] is False
    assert any("config 不是合法 JSON" in e for e in r["errors"])


def test_non_object_config_readable_error():
    r = _submit(_dag(
        nodes=[{"id": "a", "type": "tool", "label": "A", "config": [1, 2]}],
        edges=[],
    ))
    assert r["applied"] is False
    assert any("config 必须是对象" in e for e in r["errors"])


def test_deeply_malformed_payload_never_raises():
    """Every structural oddity returns applied:false — never a raw crash."""
    tool = SubmitDagStepTool()
    for bad in (
        {"nodes": [42], "edges": []},
        {"nodes": [{"id": "a", "type": "tool", "label": "A", "config": None}], "edges": [{}]},
        {"nodes": {"item": [{"id": "a", "type": "tool", "label": "A"}]}, "edges": []},
    ):
        r = json.loads(tool.execute(None, dag=bad))
        assert r["applied"] is False
        assert isinstance(r["errors"], list) and r["errors"]


def test_prompt_builder_registered():
    from strategy_research.kernel.agent.prompt_builder import PromptBuilderFactory

    assert "workflow_orchestrator" in PromptBuilderFactory.list_roles()
    prompt = PromptBuilderFactory.get("workflow_orchestrator").build_system_prompt(
        "workflow_orchestrator", {}
    )
    assert "submit_dag_step" in prompt
    assert "增量" in prompt


# ── chat loop dag: registry gating ───────────────────────────────────


def test_dag_session_registry_only_submit_tool(monkeypatch):
    """A dag:-prefixed session must expose exactly submit_dag_step."""
    from strategy_research.kernel.agent import chat_loop

    captured: dict = {}

    def fake_agent_loop(*args, **kwargs):
        captured["registry"] = kwargs.get("registry")
        captured["allowed_tools"] = kwargs.get("allowed_tools")
        captured["system_prompt"] = kwargs.get("system_prompt")
        return object()

    monkeypatch.setattr(chat_loop, "AgentLoop", fake_agent_loop)

    build_chat_agent_loop = chat_loop.build_chat_agent_loop
    # system_prompt_override short-circuits PromptBuilderFactory
    build_chat_agent_loop(
        config=None,
        session_id="dag:alpha_research",
        system_prompt_override="x",
        on_event=None,
        event_bus=None,
    )
    tools = list(captured["registry"]._tools.keys())
    assert tools == ["submit_dag_step"]
    assert captured["allowed_tools"] == ["submit_dag_step"]


def test_normal_session_registry_untouched(monkeypatch):
    from strategy_research.kernel.agent import chat_loop

    captured: dict = {}

    def fake_agent_loop(*args, **kwargs):
        captured["registry"] = kwargs.get("registry")
        return object()

    monkeypatch.setattr(chat_loop, "AgentLoop", fake_agent_loop)
    chat_loop.build_chat_agent_loop(
        config=None,
        session_id="sess-abc",
        system_prompt_override="x",
        on_event=None,
        event_bus=None,
    )
    tools = list(captured["registry"]._tools.keys())
    assert "submit_dag_step" not in tools
    assert "read" in tools


# ── drafts endpoints ─────────────────────────────────────────────────


@pytest.fixture
def draft_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SR_WORKSPACE_PATH", str(tmp_path))
    from fastapi.testclient import TestClient

    from strategy_research.interfaces.api.app import create_app

    app = create_app()
    app.state.user_id = "tester"
    return TestClient(app)


def _draft_auth_headers():
    from strategy_research.interfaces.api.auth_tokens import create_token
    return {"Authorization": f"Bearer {create_token('draft-tester')}"}


def test_draft_roundtrip(draft_client):
    h = _draft_auth_headers()
    payload = {
        "dag_id": "alpha_research",
        "nodes": [{"id": "a", "type": "llm_agent", "label": "A"}],
        "edges": [],
    }
    r = draft_client.put("/api/goal/workflow/orchestrate/draft", json=payload, headers=h)
    assert r.status_code == 200

    r = draft_client.get("/api/goal/workflow/orchestrate/draft/alpha_research", headers=h)
    assert r.status_code == 200
    assert r.json()["dag"]["nodes"][0]["id"] == "a"

    # upsert overwrites
    payload["nodes"].append({"id": "b", "type": "tool", "label": "B"})
    draft_client.put("/api/goal/workflow/orchestrate/draft", json=payload, headers=h)
    r = draft_client.get("/api/goal/workflow/orchestrate/draft/alpha_research", headers=h)
    assert len(r.json()["dag"]["nodes"]) == 2

    # delete clears
    r = draft_client.delete("/api/goal/workflow/orchestrate/draft/alpha_research", headers=h)
    assert r.status_code == 200
    r = draft_client.get("/api/goal/workflow/orchestrate/draft/alpha_research", headers=h)
    assert r.json()["dag"] is None


def test_draft_missing_returns_null(draft_client):
    r = draft_client.get("/api/goal/workflow/orchestrate/draft/never_saved", headers=_draft_auth_headers())
    assert r.status_code == 200
    assert r.json()["dag"] is None


def test_orchestrate_session_idempotent(draft_client):
    h = _draft_auth_headers()
    r1 = draft_client.post("/api/goal/workflow/orchestrate/session", json={"dag_id": "alpha_research"}, headers=h)
    assert r1.status_code == 200
    sid = r1.json()["session_id"]
    assert sid == "dag:alpha_research"

    r2 = draft_client.post("/api/goal/workflow/orchestrate/session", json={"dag_id": "alpha_research"}, headers=h)
    assert r2.status_code == 200
    assert r2.json()["session_id"] == sid


def test_orchestrate_session_invalid_dag_id(draft_client):
    r = draft_client.post("/api/goal/workflow/orchestrate/session", json={"dag_id": "a/b"}, headers=_draft_auth_headers())
    assert r.status_code == 400
