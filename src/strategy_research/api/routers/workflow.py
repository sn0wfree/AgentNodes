"""Goal Workflow API router — /api/goal/workflow/*"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()


class WorkflowStartRequest(BaseModel):
    session_id: str
    workflow_name: str
    objective: str


# In-memory store for active workflow runners. Entries are pruned when
# the workflow reaches a terminal state (checked on every access and
# in the SSE generator's finally) and after _ACTIVE_RUNNER_TTL as a
# crash/abandoned-run safety net.
_active_runners: dict[str, dict] = {}
_ACTIVE_RUNNER_TTL = 3600.0  # 1h


def _prune_runners() -> None:
    """Drop terminal + expired runner entries (bounded memory)."""
    now = time.time()
    terminal = []
    for goal_id, entry in list(_active_runners.items()):
        try:
            progress = entry["runner"].get_progress()
        except Exception:
            terminal.append(goal_id)
            continue
        if progress.get("status") in ("completed", "error") or progress.get("hook_completed"):
            terminal.append(goal_id)
        elif now - entry.get("started_at", 0) > _ACTIVE_RUNNER_TTL:
            terminal.append(goal_id)
    for goal_id in terminal:
        _active_runners.pop(goal_id, None)


def _runner_visible(entry: dict, user_id: str) -> bool:
    """P5-5③: runner entries are owner-scoped.

    owner None can only be an in-memory entry from the seconds before a
    hot-reload picks up this fix (runners are ephemeral, TTL 1h) — kept
    visible to avoid locking operators out mid-deploy.
    """
    owner = entry.get("owner")
    return owner is None or owner == user_id


async def _require_run_access(run_id: str, request: Request) -> None:
    """P5-5③: definition runs are owner-scoped via the bound session.

    Live runners carry the owner stamped at /start-definition; persisted
    records resolve ownership through their ``session_id`` (the runs
    table already stores it — no schema change needed). 404 unknown,
    403 foreign, same semantics as chat sessions.
    """
    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", None) or "anonymous"
    registry = _get_run_registry()
    runner = registry.get(run_id)
    owner = getattr(runner, "_owner", None) if runner is not None else None
    if owner == user_id:
        return
    record = _get_run_store().get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    _fetch_session_owned(_get_db(), record["session_id"], user_id)


@router.post("/start")
async def workflow_start(req: WorkflowStartRequest, request: Request):
    """Start a goal workflow by name.

    P5-5③: the caller must own the bound session; the runner entry
    records the owner so status/pause/resume/directive/events are
    object-scoped (previously ANY authenticated user could control ANY
    run via the shared in-process registry).
    """
    try:
        from ...core.goal.workflow import GoalWorkflowRunner
        from ...core.goal.workflow_config import load_goal_workflow
        from .chat import _get_session_service
        from .web_session import _fetch_session_owned, _get_db

        user_id = getattr(request.state, "user_id", None) or "anonymous"
        _fetch_session_owned(_get_db(), req.session_id, user_id)

        config = load_goal_workflow(req.workflow_name)
        session_service = _get_session_service()
        runner = GoalWorkflowRunner(
            config=config,
            session_id=req.session_id,
            session_service=session_service,
        )

        goal_id = await runner.start(req.objective)

        _active_runners[goal_id] = {
            "runner": runner,
            "session_id": req.session_id,
            "workflow_name": req.workflow_name,
            "started_at": time.time(),
            "owner": user_id,
        }

        return {
            "status": "ok",
            "goal_id": goal_id,
            "workflow_name": req.workflow_name,
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Workflow '{req.workflow_name}' not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def workflow_status(goal_id: str, request: Request):
    """Get workflow progress for a goal (owner-scoped; foreign ids and
    unknown ids return the same not_found shape — no existence oracle)."""
    _prune_runners()
    entry = _active_runners.get(goal_id)
    user_id = getattr(request.state, "user_id", None) or "anonymous"
    if entry is None or not _runner_visible(entry, user_id):
        return {"status": "not_found", "goal_id": goal_id}

    runner = entry["runner"]
    progress = runner.get_progress()
    return {
        "status": "ok",
        "goal_id": goal_id,
        "workflow_name": entry["workflow_name"],
        "progress": progress,
    }


@router.post("/pause")
async def workflow_pause(goal_id: str, request: Request, immediate: bool = False):
    """Pause a running workflow (owner-scoped)."""
    _prune_runners()
    entry = _active_runners.get(goal_id)
    user_id = getattr(request.state, "user_id", None) or "anonymous"
    if entry is None or not _runner_visible(entry, user_id):
        raise HTTPException(status_code=404, detail="Workflow not found")

    runner = entry["runner"]
    runner.pause(immediate=immediate)
    return {"status": "ok", "paused": True}


@router.post("/resume")
async def workflow_resume(goal_id: str, request: Request):
    """Resume a paused workflow (owner-scoped)."""
    _prune_runners()
    entry = _active_runners.get(goal_id)
    user_id = getattr(request.state, "user_id", None) or "anonymous"
    if entry is None or not _runner_visible(entry, user_id):
        raise HTTPException(status_code=404, detail="Workflow not found")

    runner = entry["runner"]
    runner.resume()
    return {"status": "ok", "resumed": True}


class WorkflowDirectiveRequest(BaseModel):
    content: str


@router.post("/directive")
async def workflow_directive(goal_id: str, req: WorkflowDirectiveRequest, request: Request):
    """Add a user directive to a running workflow (owner-scoped)."""
    _prune_runners()
    entry = _active_runners.get(goal_id)
    user_id = getattr(request.state, "user_id", None) or "anonymous"
    if entry is None or not _runner_visible(entry, user_id):
        raise HTTPException(status_code=404, detail="Workflow not found")

    runner = entry["runner"]
    runner.add_directive(req.content)
    return {"status": "ok", "directive_added": True}


@router.get("/list")
async def workflow_list():
    """List available workflow presets."""
    from ...core.goal.workflow_config import list_goal_workflows
    return {"status": "ok", "workflows": list_goal_workflows()}


@router.get("/{name}/graph")
async def workflow_graph(name: str):
    """Return the DAG structure (nodes + edges) for a workflow preset."""
    from ...core.goal.workflow_config import load_goal_workflow
    try:
        config = load_goal_workflow(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Workflow '{name}' not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    nodes = [{"id": a.id, "label": a.id} for a in config.agents]
    edges = [
        {"source": dep, "target": agent.id}
        for agent in config.agents
        for dep in config.dag.get(agent.id, [])
    ]
    return {
        "status": "ok",
        "name": config.name,
        "description": config.description,
        "nodes": nodes,
        "edges": edges,
    }


async def workflow_event_stream(runner):
    """Async generator streaming workflow progress as SSE payloads.

    Shared by the API SSE endpoint (/api/goal/workflow/{goal_id}/events)
    and the WebUI proxy (/workflows/{name}/events). Emits the initial
    progress snapshot, then forwards observer events with heartbeats
    every 1s; terminates on workflow_completed / workflow_failed or a
    terminal progress state.

    SSE frame conventions (frontend EVENT_TYPES contract):
      - ``event: progress`` + data: ``get_progress()`` dict (initial snapshot,
        heartbeat) — frontend updates executionProgress + agent statuses.
      - ``event: dag_update`` + data: ``{node_id, status}`` — emitted for each
        ``agent_complete`` observer event so the DAG panel can update
        individual nodes in real time without waiting for the next heartbeat.
    """
    import asyncio

    queue: asyncio.Queue = asyncio.Queue()

    def _sse(name: str, data: dict) -> str:
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

    class SSEObserver:
        def on_event(self, event: str, data: dict):
            try:
                queue.put_nowait((event, data))
            except asyncio.QueueFull:
                pass

    observer = SSEObserver()
    runner.subscribe(observer)

    try:
        yield _sse("progress", runner.get_progress())

        while True:
            try:
                event, data = await asyncio.wait_for(queue.get(), timeout=1.0)
                for frame in _handle_observer_event(event, data, runner):
                    yield frame
                if event in ("workflow_completed", "workflow_failed"):
                    break
            except asyncio.TimeoutError:
                yield _sse("progress", runner.get_progress())

                if _is_terminal(runner):
                    break
    finally:
        runner.unsubscribe(observer)
        # Terminal (or abandoned) run: drop the runner entry so
        # _active_runners cannot grow unbounded.
        _prune_runners()


def _is_terminal(runner) -> bool:
    """Whether the runner has reached a terminal progress state."""
    progress = runner.get_progress()
    return (
        progress.get("hook_completed")
        or progress.get("status") in ("completed", "error")
    )


def _handle_observer_event(event: str, data: dict, runner) -> list[str]:
    """Translate a runner observer event into one or more SSE frames."""
    frames: list[str] = []

    def _sse(name: str, payload: dict) -> str:
        return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"

    if event == "agent_complete" and isinstance(data, dict):
        agent_id = data.get("agent_id")
        if agent_id:
            frames.append(_sse("dag_update", {
                "node_id": agent_id,
                "status": "success",
            }))
    elif event == "layer_start":
        # Surface layer_start as a progress refresh so the frontend
        # sees current_layer moving forward.
        frames.append(_sse("progress", runner.get_progress()))
    elif event in ("workflow_completed", "workflow_failed"):
        frames.append(_sse("progress", runner.get_progress()))
        frames.append(_sse(event, data))
    return frames


@router.get("/events")
async def workflow_events(goal_id: str, request: Request):
    """SSE endpoint for workflow progress events (owner-scoped)."""
    entry = _active_runners.get(goal_id)
    user_id = getattr(request.state, "user_id", None) or "anonymous"
    if entry is None or not _runner_visible(entry, user_id):
        raise HTTPException(status_code=404, detail="Workflow not found")

    return StreamingResponse(
        workflow_event_stream(entry["runner"]),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Modular DAG workflows (docs/workflow-module-design.md, Commit 3)
# Endpoints: definitions CRUD / start-definition / approve / run status
# ─────────────────────────────────────────────────────────────────────

from pathlib import Path as _Path  # noqa: E402

_run_registry = None
_run_store = None


def _definition_workspace() -> _Path:
    import os
    return _Path(os.environ.get("SR_WORKSPACE_PATH", str(_Path.cwd())))


def _get_run_store() -> Any:
    global _run_store
    if _run_store is None:
        from strategy_research.kernel.workflow.store import WorkflowStore
        _run_store = WorkflowStore(db_path=_definition_workspace() / "workflows.db")
    return _run_store


def _get_run_registry() -> Any:
    global _run_registry
    if _run_registry is None:
        from strategy_research.kernel.workflow.executor import WorkflowRunRegistry
        _run_registry = WorkflowRunRegistry()
    return _run_registry


class WorkflowDefinitionPayload(BaseModel):
    name: str
    description: str = ""
    version: str = "1.0"
    budget: dict = {}
    llm: dict = {}
    params: dict = {}
    nodes: list[dict] = []
    edges: list[dict] = []


class WorkflowStartDefinitionRequest(BaseModel):
    session_id: str
    definition_name: str
    objective: str
    params: dict = {}


class WorkflowApproveRequest(BaseModel):
    run_id: str
    approved: bool
    edits: dict | None = None


# ── Orchestrator chat session + auto-save drafts ─────────────────────
# The orchestration chat reuses the chat session system with
# session_id = "dag:{definition_name}". Messages persist in the session
# DB; the canvas draft (uncommitted editor state) persists in the drafts
# table below so a refresh restores the latest state.
_DRAFT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS drafts (
    dag_id TEXT PRIMARY KEY,
    nodes_json TEXT NOT NULL,
    edges_json TEXT NOT NULL,
    updated_at REAL NOT NULL,
    owner TEXT
)
"""


def _draft_db() -> Any:
    import sqlite3
    conn = sqlite3.connect(_definition_workspace() / "workflows.db")
    conn.execute(_DRAFT_TABLE_DDL)
    # P5-5③: owner column migration for DBs created before the column
    # existed (old rows keep owner NULL → treat as unowned/shared-legacy).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(drafts)").fetchall()}
    if "owner" not in cols:
        conn.execute("ALTER TABLE drafts ADD COLUMN owner TEXT")
    conn.commit()
    return conn


class OrchestrateSessionRequest(BaseModel):
    dag_id: str


class DraftRequest(BaseModel):
    dag_id: str
    nodes: list[dict]
    edges: list[dict]


@router.post("/orchestrate/session")
async def orchestrate_session(body: OrchestrateSessionRequest, request: Request):
    """Get (or create) the DAG-bound chat session for a definition.

    session_id = "dag:{dag_id}" — the agent loop for these sessions is
    restricted to the submit_dag_step tool (see core/agent/chat_loop.py).
    """
    dag_id = body.dag_id.strip()
    if not dag_id or "/" in dag_id or "\\" in dag_id:
        raise HTTPException(status_code=400, detail="invalid dag_id")
    session_id = f"dag:{dag_id}"
    from .web_session import WebSessionCreate, create_session
    try:
        session = await create_session(WebSessionCreate(title=f"DAG 编排 · {dag_id}", id=session_id), request)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"session error: {exc}")
    return {"status": "ok", "session_id": session["id"]}


@router.put("/orchestrate/draft")
async def draft_put(body: DraftRequest, request: Request):
    """Upsert the canvas draft (auto-saved editor state).

    P5-5③: the draft is stamped with the caller's user_id; reads/deletes
    by another user return 404. Legacy rows (owner NULL, pre-fix DBs) stay
    shared-legacy: readable and claimable by the next writer.
    """
    user_id = getattr(request.state, "user_id", None) or "anonymous"
    conn = _draft_db()
    try:
        conn.execute(
            "INSERT INTO drafts (dag_id, nodes_json, edges_json, updated_at, owner) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(dag_id) DO UPDATE SET nodes_json=excluded.nodes_json, "
            "edges_json=excluded.edges_json, updated_at=excluded.updated_at, "
            "owner=excluded.owner",
            (body.dag_id, json.dumps(body.nodes, ensure_ascii=False),
             json.dumps(body.edges, ensure_ascii=False), time.time(), user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok"}


@router.get("/orchestrate/draft/{dag_id}")
async def draft_get(dag_id: str, request: Request):
    """Return the saved canvas draft, or {dag: null} when absent/foreign."""
    user_id = getattr(request.state, "user_id", None) or "anonymous"
    conn = _draft_db()
    try:
        row = conn.execute(
            "SELECT nodes_json, edges_json, owner FROM drafts WHERE dag_id = ?",
            (dag_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"dag": None}
    owner = row[2]
    if owner is not None and owner != user_id:
        return {"dag": None}  # foreign draft: same shape as absent (no oracle)
    return {"dag": {"nodes": json.loads(row[0]), "edges": json.loads(row[1])}}


@router.delete("/orchestrate/draft/{dag_id}")
async def draft_delete(dag_id: str, request: Request):
    """Clear the draft (after the definition is explicitly saved)."""
    user_id = getattr(request.state, "user_id", None) or "anonymous"
    conn = _draft_db()
    try:
        row = conn.execute(
            "SELECT owner FROM drafts WHERE dag_id = ?", (dag_id,),
        ).fetchone()
        if row is not None and row[0] is not None and row[0] != user_id:
            raise HTTPException(status_code=404, detail="Draft not found")
        conn.execute("DELETE FROM drafts WHERE dag_id = ?", (dag_id,))
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok", "cleared": dag_id}


@router.post("/definitions")
async def definitions_create(payload: WorkflowDefinitionPayload, request: Request):
    """Create or overwrite a user workflow definition."""
    from strategy_research.kernel.workflow.builtin import save_user_definition
    from strategy_research.kernel.workflow.definition import WorkflowDefinition

    definition = WorkflowDefinition.from_dict(payload.model_dump(), source="user")
    errors = definition.validate()
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    path = save_user_definition(definition, _definition_workspace())
    return {"status": "ok", "name": definition.name, "path": str(path),
            "nodes": len(definition.nodes), "edges": len(definition.edges)}


@router.get("/definitions")
async def definitions_list(request: Request):
    """List all definitions (user shadows builtin, source marked)."""
    from strategy_research.kernel.workflow.builtin import list_definitions
    return {"status": "ok", "definitions": list_definitions(_definition_workspace())}


@router.get("/definitions/{name}")
async def definitions_get(name: str, request: Request):
    """Fetch a definition by name."""
    from strategy_research.kernel.workflow.builtin import load_definition
    definition = load_definition(name, _definition_workspace())
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Definition '{name}' not found")
    return {"status": "ok", "definition": definition.to_dict()}


@router.delete("/definitions/{name}")
async def definitions_delete(name: str, request: Request, x_admin_token: str | None = Header(None)):
    """Delete a user definition (destructive → admin token required).

    P5-5③: reads stay shared (definitions are team assets, like builtins),
    but destructive writes need the admin token. Builtin definitions are
    read-only regardless.
    """
    from strategy_research.kernel.workflow.builtin import delete_user_definition, load_definition
    from .admin import _verify_admin
    _verify_admin(x_admin_token)
    definition = load_definition(name, _definition_workspace())
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Definition '{name}' not found")
    if definition.source == "builtin":
        raise HTTPException(status_code=422, detail="Builtin definitions are read-only; use /definitions/{name}/copy first")
    if delete_user_definition(name, _definition_workspace()):
        return {"status": "ok", "deleted": name}
    raise HTTPException(status_code=500, detail="Failed to delete definition")


@router.post("/definitions/{name}/copy")
async def definitions_copy(name: str, request: Request):
    """Copy a definition (typically a builtin) into the user directory."""
    import json as _json

    from strategy_research.kernel.workflow.builtin import load_definition, save_user_definition
    from strategy_research.kernel.workflow.definition import WorkflowDefinition

    definition = load_definition(name, _definition_workspace())
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Definition '{name}' not found")
    user_copy = WorkflowDefinition.from_dict(
        _json.loads(definition.to_json()), source="user",
    )
    path = save_user_definition(user_copy, _definition_workspace())
    return {"status": "ok", "name": user_copy.name, "path": str(path)}


@router.get("/definitions/{name}/graph")
async def definitions_graph(name: str, request: Request):
    """Return nodes + edges for the WorkflowDAG frontend (typed nodes)."""
    from strategy_research.kernel.workflow.builtin import load_definition
    definition = load_definition(name, _definition_workspace())
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Definition '{name}' not found")
    nodes = [{"id": n.id, "label": n.label or n.id, "type": n.type} for n in definition.nodes]
    edges = [{"source": e.source, "target": e.target} for e in definition.edges]
    return {"status": "ok", "name": definition.name,
            "description": definition.description, "nodes": nodes, "edges": edges}


@router.post("/start-definition")
async def start_definition(req: WorkflowStartDefinitionRequest, request: Request):
    """Start a modular workflow definition run (session ownership required;
    the run is owner-stamped for status/detail/events/approve access)."""
    from strategy_research.kernel.workflow.builtin import load_definition
    from strategy_research.kernel.workflow.executor import WorkflowRunner
    from strategy_research.kernel.workflow.node_types import register_builtin_tool_executors
    from .web_session import _fetch_session_owned, _get_db

    user_id = getattr(request.state, "user_id", None) or "anonymous"
    _fetch_session_owned(_get_db(), req.session_id, user_id)

    definition = load_definition(req.definition_name, _definition_workspace())
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Definition '{req.definition_name}' not found")

    store = _get_run_store()
    registry = _get_run_registry()
    register_builtin_tool_executors()
    runner = WorkflowRunner(
        definition=definition,
        workspace=_definition_workspace(),
        objective=req.objective,
        store=store,
        session_id=req.session_id,
        params_override=req.params or None,
    )
    runner._owner = user_id  # P5-5③: ownership stamp (live-run gate)
    run_id = runner.start()
    registry.put(runner)
    snapshot = runner.status_snapshot()
    return {"status": "ok", "run_id": run_id, "run": snapshot}


@router.post("/approve")
async def workflow_approve(req: WorkflowApproveRequest, request: Request):
    """Respond to a pending approval gate (owner-scoped)."""
    await _require_run_access(req.run_id, request)
    registry = _get_run_registry()
    runner = registry.get(req.run_id)
    if runner is None:
        raise HTTPException(status_code=404, detail=f"Run '{req.run_id}' not active")
    if not runner.approve(req.approved, req.edits):
        raise HTTPException(status_code=409, detail="Run is not awaiting approval")
    return {"status": "ok", "run_id": req.run_id, "run": runner.status_snapshot()}


@router.get("/run/{run_id}/status")
async def run_status(run_id: str, request: Request):
    """Run status: live snapshot if active, else persisted record."""
    await _require_run_access(run_id, request)
    store = _get_run_store()
    registry = _get_run_registry()
    runner = registry.get(run_id)
    if runner is not None:
        return {"status": "ok", "run_id": run_id, "run": runner.status_snapshot()}
    record = store.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return {"status": "ok", "run_id": run_id, "run": record}


@router.get("/run/{run_id}")
async def run_detail(run_id: str, request: Request):
    """Run detail: lifecycle + segments + node outputs + approvals."""
    await _require_run_access(run_id, request)
    store = _get_run_store()
    record = store.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return {
        "status": "ok",
        "run": record,
        "segments": store.list_segments(run_id),
        "node_outputs": store.list_node_outputs(run_id),
        "approvals": [
            store.get_approval(run_id, node_id) or {"run_id": run_id, "node_id": node_id}
            for node_id in _approval_node_ids(store, run_id)
        ],
    }


def _approval_node_ids(store: Any, run_id: str) -> list[str]:
    rows = store._ensure_conn().execute(
        "SELECT node_id FROM approvals WHERE run_id = ? ORDER BY created_at", (run_id,),
    ).fetchall()
    return [row["node_id"] for row in rows]


@router.get("/run/{run_id}/events")
async def run_events(run_id: str, request: Request):
    """SSE event history (polling-based, works after process restart)."""
    await _require_run_access(run_id, request)
    store = _get_run_store()

    async def _stream():
        import asyncio as _asyncio
        last_seq = 0
        while True:
            events = store.list_events(run_id, limit=500)
            for event in reversed(events):
                if event["seq"] <= last_seq:
                    continue
                last_seq = event["seq"]
                yield f"event: {event['event_type']}\ndata: {json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
            record = store.get_run(run_id)
            if record and record["status"] in ("completed", "failed", "cancelled"):
                yield f"event: run_terminal\ndata: {json.dumps({'status': record['status']}, ensure_ascii=False)}\n\n"
                return
            try:
                await _asyncio.wait_for(_asyncio.Future(), timeout=1.0)
            except _asyncio.TimeoutError:
                continue

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


@router.delete("/run/{run_id}")
async def run_delete(run_id: str, request: Request):
    """Delete run history (owner-scoped, all related rows)."""
    await _require_run_access(run_id, request)
    store = _get_run_store()
    if store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    registry = _get_run_registry()
    registry.pop(run_id)
    store.delete_run(run_id)
    return {"status": "ok", "deleted": run_id}


__all__ = ["router"]
