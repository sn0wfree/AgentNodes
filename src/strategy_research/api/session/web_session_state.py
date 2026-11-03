"""Frontend-facing session state shaping (goal / workflow / agents).

Extracted from api/routers/web_session.py in the Phase 2 split
(docs/phase2-web-session-projector-design.md §2.2). Pure dict ->
dict transforms plus the active-runner lookup; no persistence.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import Request

logger = logging.getLogger(__name__)


def _build_goal_snapshot(request: Request, session_id: str) -> dict | None:
    """Return the current goal snapshot dict for a session, or None.

    Pulls goal + criteria + evidence_count from GoalStore. The frontend
    Goal store expects a specific shape — the mapping in
    ``_shape_goal_for_frontend`` adapts the snapshot fields for that
    contract.
    """
    try:
        from ...core.goal import GoalStore

        db_path = getattr(request.app.state, "goal_db_path", None)
        with GoalStore(db_path=db_path) as store:
            return store.get_current_snapshot(session_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("[state] goal snapshot failed: %s", e)
        return None


def _shape_goal_for_frontend(snapshot: dict | None) -> dict | None:
    """Map a GoalStore snapshot to the frontend Goal store shape.

    Frontend fields (see webui stores/goal.ts):
      goal_id, session_id, status, objective, progress_percent,
      criteria[{criterion_id, text, status, evidence_count}],
      evidence_count
    """
    if not snapshot:
        return None
    goal = snapshot.get("goal") or {}
    criteria = snapshot.get("criteria") or []
    return {
        "goal_id": goal.get("goal_id"),
        "session_id": goal.get("session_id", ""),
        "status": goal.get("status", "active"),
        "objective": goal.get("objective", ""),
        "progress_percent": _compute_goal_progress_percent(criteria),
        "criteria": [
            {
                "criterion_id": c.get("criterion_id", ""),
                "text": c.get("text", ""),
                "status": c.get("status", "pending"),
                "evidence_count": c.get("evidence_count", 0),
            }
            for c in criteria
        ],
        "evidence_count": snapshot.get("evidence_count", 0),
        "recap": goal.get("recap"),
    }


def _compute_goal_progress_percent(criteria: list[dict]) -> int:
    """0..100 — share of criteria with status 'covered' or 'complete'."""
    if not criteria:
        return 0
    done = sum(1 for c in criteria if c.get("status") in ("covered", "complete"))
    return int(round(done / len(criteria) * 100))


def _find_active_workflow_runner(session_id: str) -> tuple[str, Any] | None:
    """Find an active workflow runner (goal_id, runner) for a session.

    Walks ``routers.workflow._active_runners`` (in-memory). The runner's
    session_id is stored in the entry's ``session_id`` field — if it
    matches we return ``(goal_id, runner)``.
    """
    try:
        from ..routers.workflow import _active_runners, _prune_runners

        _prune_runners()
        for goal_id, entry in list(_active_runners.items()):
            if entry.get("session_id") == session_id:
                return goal_id, entry["runner"]
    except Exception as e:  # noqa: BLE001
        logger.debug("[state] workflow runner lookup failed: %s", e)
    return None


def _shape_workflow_for_frontend(
    runner: Any,
    workflow_name: str,
) -> dict:
    """Map a workflow runner + its config to the frontend shape.

    Returns ``{name, nodes[], edges[], progress, agent_statuses}``.
    The frontend's workflow store expects ``nodes=[{id,label,status}]``
    and ``edges=[{id,source,target}]``; ``agent_statuses`` is the live
    runner dict used to derive per-node status.
    """
    config = getattr(runner, "_config", None)
    agent_status_map: dict[str, str] = {}
    progress_data: dict = {}
    try:
        progress_data = runner.get_progress() or {}
        agent_status_map = progress_data.get("agent_statuses", {}) or {}
    except Exception as e:  # noqa: BLE001
        logger.debug("[state] get_progress failed: %s", e)

    cfg_agents = getattr(config, "agents", []) if config else []
    cfg_dag = getattr(config, "dag", {}) if config else {}

    nodes = []
    for agent_cfg in cfg_agents:
        agent_id = getattr(agent_cfg, "id", None) or ""
        # Map backend status → frontend DAG node status (pending |
        # running | completed | failed). Backend uses pending|running|
        # success|skipped|error; we collapse success+skipped →
        # completed and error → failed.
        raw = agent_status_map.get(agent_id, "pending")
        node_status = _MAP_AGENT_STATUS_TO_NODE.get(raw, "pending")
        nodes.append({
            "id": agent_id,
            "label": agent_id,
            "type": "agent",
            "status": node_status,
        })

    edges = []
    for src, targets in (cfg_dag or {}).items():
        for tgt in targets or []:
            edges.append({"id": f"{src}->{tgt}", "source": src, "target": tgt})

    return {
        "name": workflow_name,
        "nodes": nodes,
        "edges": edges,
        "progress": progress_data,
        "agent_statuses": agent_status_map,
    }


_MAP_AGENT_STATUS_TO_NODE = {
    "pending": "pending",
    "queued": "pending",
    "running": "running",
    "success": "completed",
    "skipped": "completed",
    "error": "failed",
    "completed": "completed",
    "failed": "failed",
}


def _shape_agents_for_frontend(
    session_id: str,
    workflow: dict | None,
) -> list[dict]:
    """Build the per-session Agent store entries.

    Each entry matches the Agent interface in stores/agents.ts minimal
    fields: id, session_id, status, name, created_at, updated_at,
    tool_calls_count, compaction_count, context_tokens,
    context_tokens_limit, iterations_detail. We populate only what we
    can derive from the workflow snapshot — the SSE stream refreshes
    the rest after the backfill (e.g. token usage, iteration detail).
    """
    if not workflow:
        return []
    status_map = workflow.get("agent_statuses", {}) or {}
    nodes = workflow.get("nodes") or []
    now = time.time()
    agents = []
    for node in nodes:
        agent_id = node["id"]
        raw_status = status_map.get(agent_id, "pending")
        # Map backend status → frontend AgentStatus
        fe_status = _MAP_AGENT_STATUS_TO_AGENT.get(raw_status, "pending")
        agents.append({
            "id": agent_id,
            "session_id": session_id,
            "status": fe_status,
            "name": agent_id,
            "description": "",
            "created_at": now,
            "updated_at": now,
            "tool_calls_count": 0,
            "compaction_count": 0,
            "context_tokens": 0,
            "context_tokens_limit": 0,
            "iterations_detail": [],
        })
    return agents


_MAP_AGENT_STATUS_TO_AGENT = {
    "pending": "pending",
    "queued": "pending",
    "running": "running",
    "success": "completed",
    "skipped": "completed",
    "error": "failed",
    "completed": "completed",
    "failed": "failed",
    "aborted": "aborted",
}


def _shape_workflow_for_frontend_from_config(cfg: Any, workflow_name: str) -> dict:
    """Build the workflow subsection from a config-only source.

    Used when there is no in-memory runner — the DAG and agent roster
    still come from the workflow's YAML. All agent statuses default to
    ``pending`` and ``progress`` is null (no live runner to ask).
    """
    cfg_agents = getattr(cfg, "agents", []) or []
    cfg_dag = getattr(cfg, "dag", {}) or {}
    nodes = []
    for agent_cfg in cfg_agents:
        agent_id = getattr(agent_cfg, "id", "") or ""
        nodes.append({"id": agent_id, "label": agent_id, "type": "agent", "status": "pending"})
    edges = []
    for src, targets in cfg_dag.items():
        for tgt in targets or []:
            edges.append({"id": f"{src}->{tgt}", "source": src, "target": tgt})
    return {
        "name": workflow_name,
        "nodes": nodes,
        "edges": edges,
        "progress": None,
        "agent_statuses": {},
    }
