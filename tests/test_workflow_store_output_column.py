"""R-B0: node_outputs carries the full output so a run can be rebuilt.

docs/plan-retry-fix-and-workflow-migration-20261031.md §决定 B：
审批续跑在进程重启后要能恢复，而 store 此前只存 summary 不存 output
（下游节点靠 AgentResult.output 做 upstream）。本刀补列 + 迁移 + 重建 API。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from strategy_research.core.swarm.runtime import AgentResult, AgentStatus
from strategy_research.core.workflow.store import WorkflowStore


def _store(tmp_path: Path) -> WorkflowStore:
    return WorkflowStore(db_path=tmp_path / "workflows.db")


def _result(node_id="n1", output="hello", status=AgentStatus.SUCCESS, **kw):
    return AgentResult(
        agent_id=node_id, status=status, output=output,
        summary=kw.pop("summary", output[:20]), **kw,
    )


def test_output_column_round_trips(tmp_path):
    store = _store(tmp_path)
    store.create_run("r1", "def", "s", "obj", {"params": {}})
    store.save_node_output("r1", 0, _result(output="full text payload"))

    rows = store.list_node_outputs("r1")
    assert len(rows) == 1
    assert rows[0]["output"] == "full text payload"
    # summary 仍保留（老语义不变）
    assert rows[0]["summary"]


def test_output_and_summary_are_distinct(tmp_path):
    """summary 可能是截断版；output 必须是完整的。"""
    store = _store(tmp_path)
    store.create_run("r1", "def", "s", "obj", {})
    store.save_node_output(
        "r1", 0,
        _result(output="x" * 500, summary="x" * 20),
    )
    row = store.list_node_outputs("r1")[0]
    assert len(row["output"]) == 500
    assert len(row["summary"]) == 20


def test_rebuild_agent_results_restores_success_only(tmp_path):
    store = _store(tmp_path)
    store.create_run("r1", "def", "s", "obj", {})
    store.save_node_output("r1", 0, _result("ok_node", "done", summary="d"))
    store.save_node_output(
        "r1", 1,
        _result("bad_node", "", status=AgentStatus.ERROR, error="boom"),
    )

    rebuilt = store.rebuild_agent_results("r1")
    assert set(rebuilt) == {"ok_node"}          # 失败节点不恢复 → 续跑时重跑
    ar = rebuilt["ok_node"]
    assert isinstance(ar, AgentResult)
    assert ar.status == AgentStatus.SUCCESS
    assert ar.output == "done"


def test_rebuild_agent_results_parses_artifacts_and_metrics(tmp_path):
    store = _store(tmp_path)
    store.create_run("r1", "def", "s", "obj", {})
    store.save_node_output(
        "r1", 0,
        _result("n", "out", artifacts={"steps": [1, 2]}, metrics={"ic": 0.03}),
    )
    ar = store.rebuild_agent_results("r1")["n"]
    assert ar.artifacts == {"steps": [1, 2]}
    assert ar.metrics == {"ic": 0.03}


def test_rebuild_agent_results_corrupt_json_is_tolerated(tmp_path):
    """历史行/手工改库可能留下非 JSON —— 不能炸，降级为 {}。"""
    store = _store(tmp_path)
    store.create_run("r1", "def", "s", "obj", {})
    store.save_node_output("r1", 0, _result("n", "out"))
    store._ensure_conn().execute(
        "UPDATE node_outputs SET artifacts = 'not json' WHERE run_id = 'r1'"
    )
    store._ensure_conn().commit()

    ar = store.rebuild_agent_results("r1")["n"]
    assert ar.artifacts == {}


def test_migration_adds_output_column_to_legacy_db(tmp_path):
    """老库（无 output 列）打开时自动 ALTER，且不丢已有行。"""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE node_outputs (
            run_id       TEXT NOT NULL,
            segment_idx  INTEGER NOT NULL,
            node_id      TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'pending',
            summary      TEXT NOT NULL DEFAULT '',
            artifacts    TEXT NOT NULL DEFAULT '{}',
            metrics      TEXT NOT NULL DEFAULT '{}',
            error        TEXT,
            elapsed_s    REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (run_id, node_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO node_outputs (run_id, segment_idx, node_id, status, summary) "
        "VALUES ('r1', 0, 'old_node', 'success', 'legacy summary')"
    )
    conn.commit()
    conn.close()

    store = WorkflowStore(db_path=db)
    rows = store.list_node_outputs("r1")
    assert len(rows) == 1
    assert rows[0]["summary"] == "legacy summary"
    assert rows[0]["output"] == ""   # 老行无 output → 空串，不炸

    # 迁移后新写入正常
    store.save_node_output("r1", 1, _result("new_node", "fresh"))
    assert store.list_node_outputs("r1")[1]["output"] == "fresh"


def test_migration_is_idempotent(tmp_path):
    """重复打开同一库不重复 ALTER（也不报错）。"""
    db = tmp_path / "w.db"
    s1 = WorkflowStore(db_path=db)
    s1.create_run("r1", "d", "s", "o", {})
    s1.close()
    s2 = WorkflowStore(db_path=db)
    assert s2.health_check()
    assert s2.get_run("r1") is not None
