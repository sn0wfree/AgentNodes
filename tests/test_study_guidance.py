"""Study v2 M6 tests — guidance human decision points (design §13).

Covers guidance.py pure functions (frontmatter parse, two-layer load,
render/applies_to, gate violations) and the runner integration: injection
into current_state, hard-check forcing verdict=discard with manifest
gates[] records, metric-missing skip.
"""

from __future__ import annotations

import pytest

from strategy_research.apps.study.method import guidance as gd

# ── frontmatter parsing ─────────────────────────────────────────────


def test_parse_full_frontmatter():
    text = (
        "---\n"
        "gates:\n"
        '  - {id: risk-max-dd, metric: max_dd, op: ">=", value: -0.15, enforce: true, action: reject}\n'
        "  - {id: turnover-limit, metric: turnover, op: '<=' , value: 3.0, enforce: false, action: warn}\n"
        "---\n"
        "# 研究指引\n\n正文规则\n"
    )
    gates, body = gd.parse_guidance(text)
    assert len(gates) == 2
    assert gates[0].id == "risk-max-dd"
    assert gates[0].metric == "max_dd"
    assert gates[0].op == ">="
    assert gates[0].value == -0.15
    assert gates[0].enforce is True
    assert gates[1].enforce is False
    assert "研究指引" in body


def test_parse_no_frontmatter():
    gates, body = gd.parse_guidance("# 研究指引\n\n## 偏好\n- 可解释性\n")
    assert gates == []
    assert "可解释性" in body


def test_parse_malformed_yaml_degrades_to_body(tmp_path):
    text = "---\n{not yaml [\n---\n正文仍在\n"
    gates, body = gd.parse_guidance(text)
    assert gates == []
    assert "正文" in body


def test_parse_bad_gate_skipped():
    text = (
        "---\n"
        "gates:\n"
        '  - {id: ok, metric: calmar, op: ">=", value: 1.0}\n'
        '  - {id: bad-value, metric: sharpe, op: ">=", value: "abc"}\n'
        '  - {id: bad-op, metric: sharpe, op: "~", value: 1.0}\n'
        '  - {id: no-metric, op: ">=", value: 1.0}\n'
        "---\n"
        "body\n"
    )
    gates, _ = gd.parse_guidance(text)
    assert [g.id for g in gates] == ["ok"]


# ── two-layer load ──────────────────────────────────────────────────


def test_load_task_file_wins(tmp_path):
    task = tmp_path / "study" / "s1" / "guidance.md"
    task.parent.mkdir(parents=True, exist_ok=True)
    task.write_text("---\ngates:\n  - {id: g1, metric: calmar, op: '>=', value: 0.5}\n---\ntask body\n", encoding="utf-8")
    global_f = tmp_path / "study" / "guidance.md"
    global_f.parent.mkdir(parents=True, exist_ok=True)
    global_f.write_text("global body\n", encoding="utf-8")

    g = gd.load_guidance(tmp_path, "s1")
    assert g.task_scope is True
    assert g.source == task
    assert g.body.strip() == "task body"
    assert [x.id for x in g.gates] == ["g1"]


def test_load_falls_back_to_global(tmp_path):
    global_f = tmp_path / "study" / "guidance.md"
    global_f.parent.mkdir(parents=True, exist_ok=True)
    global_f.write_text("global body\n", encoding="utf-8")

    g = gd.load_guidance(tmp_path, "s2")
    assert g.task_scope is False
    assert g.source == global_f
    assert "global body" in g.body


def test_load_missing_returns_empty(tmp_path):
    g = gd.load_guidance(tmp_path, "s3")
    assert g.source is None
    assert g.gates == []
    assert g.body == ""
    assert not g.has_content


def test_load_global_only_without_study_id(tmp_path):
    global_f = tmp_path / "study" / "guidance.md"
    global_f.parent.mkdir(parents=True, exist_ok=True)
    global_f.write_text("g body\n", encoding="utf-8")
    g = gd.load_guidance(tmp_path)
    assert g.source == global_f


# ── render / applies_to ─────────────────────────────────────────────


def test_render_section_with_gates_and_body():
    gates, body = gd.parse_guidance(
        "---\ngates:\n  - {id: dd, metric: max_dd, op: '>=', value: -0.15}\n---\n## 偏好\n- x\n"
    )
    g = gd.Guidance(gates=gates, body=body)
    section = gd.render_guidance_section(g)
    assert section.startswith("## 人类判断点")
    assert "硬性规则" in section
    assert "dd: max_dd >= -0.15" in section
    assert "偏好" in section


def test_render_applies_to_filter():
    gates, _ = gd.parse_guidance(
        "---\ngates:\n"
        "  - {id: all, metric: calmar, op: '>=', value: 0.5}\n"
        "  - {id: researcher-only, metric: turnover, op: '<=', value: 3.0, applies_to: [researcher]}\n"
        "---\nbody\n"
    )
    g = gd.Guidance(gates=gates, body="body")
    researcher = gd.render_guidance_section(g, agent_name="researcher")
    assert "researcher-only" in researcher
    assert "all" in researcher
    strategist = gd.render_guidance_section(g, agent_name="strategist")
    assert "researcher-only" not in strategist
    assert "all" in strategist


def test_render_empty_guidance():
    assert gd.render_guidance_section(gd.Guidance()) == ""


# ── gate hard check ─────────────────────────────────────────────────


def test_check_violations_hit():
    gates, _ = gd.parse_guidance(
        "---\ngates:\n  - {id: dd, metric: max_dd, op: '>=', value: -0.15}\n---\n"
    )
    violations, skipped = gd.check_violations(gates, {"max_dd": -0.2})
    assert skipped == []
    assert len(violations) == 1
    v = violations[0]
    assert v["id"] == "dd"
    assert v["enforced"] is True
    assert v["result"] == "violated"
    assert v["actual"] == -0.2
    # passing metrics → no violation
    violations2, _ = gd.check_violations(gates, {"max_dd": -0.1})
    assert violations2 == []


def test_check_violations_ops():
    gates, _ = gd.parse_guidance(
        "---\ngates:\n"
        "  - {id: le, metric: turnover, op: '<=', value: 3.0}\n"
        "  - {id: gt, metric: calmar, op: '>', value: 0.5}\n"
        "  - {id: lt, metric: sharpe, op: '<', value: 2.0}\n"
        "  - {id: eq, metric: n, op: '==', value: 5.0}\n"
        "---\n"
    )
    violations, _ = gd.check_violations(gates, {"turnover": 3.5, "calmar": 0.4, "sharpe": 2.0, "n": 4.0})
    assert {v["id"] for v in violations} == {"le", "gt", "lt", "eq"}
    violations2, _ = gd.check_violations(gates, {"turnover": 2.5, "calmar": 0.6, "sharpe": 1.5, "n": 5.0})
    assert violations2 == []


def test_check_violations_metric_missing_skips():
    gates, _ = gd.parse_guidance(
        "---\ngates:\n  - {id: turnover-limit, metric: turnover, op: '<=', value: 3.0}\n---\n"
    )
    violations, skipped = gd.check_violations(gates, {"calmar": 1.0})
    assert violations == []
    assert skipped == ["turnover-limit"]


def test_check_violations_ignores_warn_gates():
    gates, _ = gd.parse_guidance(
        "---\ngates:\n  - {id: soft, metric: max_dd, op: '>=', value: -0.1, enforce: false}\n---\n"
    )
    violations, skipped = gd.check_violations(gates, {"max_dd": -0.5})
    assert violations == []
    assert skipped == []


# ── runner integration ──────────────────────────────────────────────


def test_runner_gate_hard_check_forces_discard(tmp_path, monkeypatch):
    import asyncio

    import strategy_research.apps.autoresearch as ar_mod
    from strategy_research.apps.study.bootstrap import init_study_dir as _init_study_dir
    from strategy_research.apps.goal import GoalStore
    from strategy_research.apps.goal.context import default_goal_criteria
    from strategy_research.apps.study import round_manifest as rm
    from strategy_research.apps.study import runner as runner_mod
    from strategy_research.apps.study import state_store as ss
    from strategy_research.apps.study.runner import AutoresearchRunner, ControlToken
    from strategy_research.apps.study.scheduler import NullEmitter
    from strategy_research.apps.study.store import StudyStore

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    ws = tmp_path / "ws"
    (ws / "strategies" / "demo").mkdir(parents=True)
    (ws / "strategies" / "demo" / "strategy.py").write_text("PARAMS = {}\n")

    gs = GoalStore()
    store = StudyStore()
    goal = gs.replace_goal(
        session_id="study_gate", objective="x",
        criteria=default_goal_criteria(), supersede=False,
    )
    study = store.create_study(
        owner_session_id="s", goal_id=goal.goal_id, objective="x",
        workspace_path=str(ws), strategy_name="demo",
        metric_targets=[{"name": "calmar", "op": ">=", "value": 99.0}],
        cooldown_base=0.01, cooldown_jitter=0.01, min_cooldown=0.01,
        max_rounds=1,
    )
    store.update_goal_id(study.study_id, goal.goal_id)
    _init_study_dir(ws, study.study_id, "demo", "x", guidance_md=(
        "---\n"
        "gates:\n"
        "  - {id: risk-max-dd, metric: max_dd, op: '>=', value: -0.15}\n"
        "---\n"
        "# 研究指引\n\n禁止 MaxDD 低于 -0.15\n"
    ))

    captured: dict = {}

    def rp(path, strategy, state, run_dir, **kw):
        captured["human_guidance"] = state.get("human_guidance")
        return {"researcher_output": {"hypothesis": "h1", "predicted_affected": ["calmar"]}}

    def ep(path, strategy, state, researcher, run_dir, **kw):
        return {"data_quality_output": {}, "factor_analyst_output": {},
                "strategist_output": {"action": "optimize", "changes": []},
                "portfolio_construction_output": {},
                "backtest_result": {"success": True, "run": "run_0001",
                                    "metrics": {"max_dd": -0.3}},
                "metrics": {"calmar": 0.1, "sharpe": 0.0, "max_dd": -0.3},
                "backtest_error": None}

    class D:
        def to_dict(self):
            return {"stagnation_triggered": False, "reason": "ok"}

    def ev(path, strategy, backtest, metrics, run_dir, **kw):
        return {"risk_controller_output": {}, "attribution_analyst_output": {},
                "anti_overfit_analyst_output": {}, "backtest_diagnostics_output": {},
                "decision": D(), "verdict": "keep"}

    ar_mod.run_researcher_phase = rp
    ar_mod.run_execution_phase = ep
    ar_mod.run_evaluation_phase = ev
    runner_mod.AutoresearchRunner._round_cooldown = lambda self: 0.0
    runner_mod.AutoresearchRunner._maybe_load_previous_summary = lambda self, s: None

    runner = AutoresearchRunner(study, store, control=ControlToken(), emitter=NullEmitter())

    async def main():
        reason = await runner.run()
        assert reason == "max_rounds"
        # guidance injected into current_state for every agent
        assert "human_guidance" in captured
        assert "禁止 MaxDD" in captured["human_guidance"]
        assert "硬性规则" in captured["human_guidance"]
        # hard check forced discard + manifest gates[] record
        st = ss.load(ws, study.study_id)
        m = rm.load_manifest(ws, study.study_id, st.last_completed_round)
        assert m is not None
        assert m["verdict"]["decision"] == "discard"
        assert "risk-max-dd" in m["verdict"]["reason"]
        gates = m["gates"]
        assert gates and gates[0]["id"] == "risk-max-dd"
        assert gates[0]["enforced"] is True
        assert gates[0]["result"] == "violated"

    asyncio.run(main())


def test_runner_gate_no_violation_keeps_verdict(tmp_path, monkeypatch):
    import asyncio

    import strategy_research.apps.autoresearch as ar_mod
    from strategy_research.apps.study.bootstrap import init_study_dir as _init_study_dir
    from strategy_research.apps.goal import GoalStore
    from strategy_research.apps.goal.context import default_goal_criteria
    from strategy_research.apps.study import round_manifest as rm
    from strategy_research.apps.study import runner as runner_mod
    from strategy_research.apps.study import state_store as ss
    from strategy_research.apps.study.runner import AutoresearchRunner, ControlToken
    from strategy_research.apps.study.scheduler import NullEmitter
    from strategy_research.apps.study.store import StudyStore

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    ws = tmp_path / "ws"
    (ws / "strategies" / "demo").mkdir(parents=True)
    (ws / "strategies" / "demo" / "strategy.py").write_text("PARAMS = {}\n")

    gs = GoalStore()
    store = StudyStore()
    goal = gs.replace_goal(
        session_id="study_gate_ok", objective="x",
        criteria=default_goal_criteria(), supersede=False,
    )
    study = store.create_study(
        owner_session_id="s", goal_id=goal.goal_id, objective="x",
        workspace_path=str(ws), strategy_name="demo",
        metric_targets=[{"name": "calmar", "op": ">=", "value": 99.0}],
        cooldown_base=0.01, cooldown_jitter=0.01, min_cooldown=0.01,
        max_rounds=1,
    )
    store.update_goal_id(study.study_id, goal.goal_id)
    _init_study_dir(ws, study.study_id, "demo", "x", guidance_md=(
        "---\n"
        "gates:\n"
        "  - {id: risk-max-dd, metric: max_dd, op: '>=', value: -0.15}\n"
        "---\n"
    ))

    def rp(path, strategy, state, run_dir, **kw):
        return {"researcher_output": {"hypothesis": "h2", "predicted_affected": ["calmar"]}}

    def ep(path, strategy, state, researcher, run_dir, **kw):
        return {"data_quality_output": {}, "factor_analyst_output": {},
                "strategist_output": {"action": "optimize", "changes": []},
                "portfolio_construction_output": {},
                "backtest_result": {"success": True, "run": "run_0001",
                                    "metrics": {"max_dd": -0.1}},
                "metrics": {"calmar": 0.1, "sharpe": 0.0, "max_dd": -0.1},
                "backtest_error": None}

    class D:
        def to_dict(self):
            return {"stagnation_triggered": False, "reason": "ok"}

    def ev(path, strategy, backtest, metrics, run_dir, **kw):
        return {"risk_controller_output": {}, "attribution_analyst_output": {},
                "anti_overfit_analyst_output": {}, "backtest_diagnostics_output": {},
                "decision": D(), "verdict": "keep"}

    ar_mod.run_researcher_phase = rp
    ar_mod.run_execution_phase = ep
    ar_mod.run_evaluation_phase = ev
    runner_mod.AutoresearchRunner._round_cooldown = lambda self: 0.0
    runner_mod.AutoresearchRunner._maybe_load_previous_summary = lambda self, s: None

    runner = AutoresearchRunner(study, store, control=ControlToken(), emitter=NullEmitter())

    async def main():
        reason = await runner.run()
        assert reason == "max_rounds"
        st = ss.load(ws, study.study_id)
        m = rm.load_manifest(ws, study.study_id, st.last_completed_round)
        assert m is not None
        assert m["verdict"]["decision"] == "keep"
        assert m["gates"] == []

    asyncio.run(main())


# ── §17.1 CLI input sources: compose_guidance_text ───────────────────


def test_compose_no_sources_uses_global_template(tmp_path):
    gdir = tmp_path / "study"
    gdir.mkdir(parents=True)
    (gdir / "guidance.md").write_text(
        "---\ngates:\n  - {id: g1, metric: calmar, op: '>=', value: 0.5}\n---\ntemplate body\n",
        encoding="utf-8",
    )
    text = gd.compose_guidance_text(tmp_path)
    assert text is not None
    assert "g1" in text and "template body" in text


def test_compose_no_template_returns_none(tmp_path):
    assert gd.compose_guidance_text(tmp_path) is None


def test_compose_guidance_file_replaces_body(tmp_path):
    gdir = tmp_path / "study"
    gdir.mkdir(parents=True)
    (gdir / "guidance.md").write_text(
        "---\ngates:\n  - {id: g1, metric: calmar, op: '>=', value: 0.5}\n---\ntemplate body\n",
        encoding="utf-8",
    )
    (tmp_path / "my_guide.md").write_text("自定义正文\n", encoding="utf-8")
    text = gd.compose_guidance_text(tmp_path, guidance_file="my_guide.md")
    assert "自定义正文" in text
    assert "g1" in text          # template frontmatter kept
    assert "template body" not in text


def test_compose_gates_file_replaces_frontmatter(tmp_path):
    (tmp_path / "gates.yaml").write_text(
        "gates:\n  - {id: my-gate, metric: max_dd, op: '>=', value: -0.2}\n",
        encoding="utf-8",
    )
    (tmp_path / "study").mkdir(parents=True)
    (tmp_path / "study" / "guidance.md").write_text(
        "---\nold frontmatter\n---\ntemplate body\n",
        encoding="utf-8",
    )
    text = gd.compose_guidance_text(tmp_path, gates_file="gates.yaml")
    assert text.startswith("---\ngates:")
    assert "my-gate" in text
    assert "old frontmatter" not in text
    assert "template body" in text   # template body kept
    # composed text parses back into the gate
    gates, body = gd.parse_guidance(text)
    assert [g.id for g in gates] == ["my-gate"]
    assert "template body" in body


def test_compose_both_files(tmp_path):
    (tmp_path / "gates.yaml").write_text("gates:\n  - {id: g, metric: calmar, op: '>=', value: 1.0}\n", encoding="utf-8")
    (tmp_path / "my_guide.md").write_text("全部自定义\n", encoding="utf-8")
    text = gd.compose_guidance_text(
        tmp_path, guidance_file="my_guide.md", gates_file="gates.yaml",
    )
    assert text.startswith("---\ngates:")
    assert "全部自定义" in text


def test_compose_rejects_path_escape(tmp_path):
    outside = tmp_path / "secret.md"
    outside.write_text("secret\n", encoding="utf-8")
    with pytest.raises(ValueError, match="outside workspace"):
        gd.compose_guidance_text(tmp_path, guidance_file="../secret.md")


def test_compose_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        gd.compose_guidance_text(tmp_path, guidance_file="nope.md")


# ── CLI /study start --guidance-file/--gates-file (§17.1) ───────────


class TestCliGuidanceFlags:
    def test_parse_study_flags(self):
        from strategy_research.api.routers.slash_commands import _parse_study_flags
        flags = _parse_study_flags([
            "--workspace", "/ws", "--strategy", "demo",
            "--guidance-file", "docs/guide.md",
            "--gates-file", "docs/gates.yaml",
            "--max-rounds", "3",
        ])
        assert flags["guidance_file"] == "docs/guide.md"
        assert flags["gates_file"] == "docs/gates.yaml"
        assert flags["max_rounds"] == 3
        assert flags["strategy_name"] == "demo"

    def test_start_writes_composed_guidance(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
        monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
        ws = tmp_path / "ws"
        strat_dir = ws / "strategies" / "demo"
        strat_dir.mkdir(parents=True)
        (strat_dir / "strategy.py").write_text("PARAMS = {}\n", encoding="utf-8")
        (ws / "guide.md").write_text("我的研究指南正文\n", encoding="utf-8")
        (ws / "gates.yaml").write_text(
            "gates:\n  - {id: risk-max-dd, metric: max_dd, op: '>=', value: -0.15}\n",
            encoding="utf-8",
        )

        from strategy_research.api.routers.slash_commands import _study_start_cmd
        resp = _study_start_cmd(
            ["研究目标", "--workspace", str(ws), "--strategy", "demo",
             "--guidance-file", "guide.md", "--gates-file", "gates.yaml",
             "--max-rounds", "1"],
            "sess-cli-g",
        )
        assert resp.startswith("Study created"), resp
        # guidance.md written per task with merged content
        study_dir = next((ws / "study").iterdir())
        guidance_p = study_dir / "guidance.md"
        assert guidance_p.exists()
        text = guidance_p.read_text(encoding="utf-8")
        assert text.startswith("---\ngates:")
        assert "risk-max-dd" in text
        assert "我的研究指南正文" in text

    def test_start_rejects_path_escape(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
        monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
        ws = tmp_path / "ws"
        strat_dir = ws / "strategies" / "demo"
        strat_dir.mkdir(parents=True)
        (strat_dir / "strategy.py").write_text("PARAMS = {}\n", encoding="utf-8")
        (tmp_path / "evil.md").write_text("x", encoding="utf-8")

        from strategy_research.api.routers.slash_commands import _study_start_cmd
        resp = _study_start_cmd(
            ["t", "--workspace", str(ws), "--strategy", "demo",
             "--guidance-file", "../evil.md", "--max-rounds", "1"],
            "sess-cli-x",
        )
        assert "Cannot create study" in resp
        assert "outside workspace" in resp
