"""Goal API router — /api/goal/*"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


def _session_owned(request: Request, session_id: str) -> None:
    """Enforce that ``session_id`` belongs to the requesting user (IDOR).

    Reuses web_session's ownership check so goal endpoints scoped to a
    session can't read/mutate another user's goals. Same opt-out env as
    study isolation — ``AN_ENFORCE_STUDY_IDOR=0`` (canonical) with the
    historical ``SR_ENFORCE_STUDY_IDOR`` still honored.
    """
    import os as _os

    if _os.environ.get("AN_ENFORCE_STUDY_IDOR", "").strip() == "0":
        return
    if _os.environ.get("SR_ENFORCE_STUDY_IDOR", "1") == "0":
        return
    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", None) or "anonymous"
    _fetch_session_owned(_get_db(), session_id, user_id)


class GoalStartRequest(BaseModel):
    session_id: str
    objective: str
    risk_tier: str = "research_general"
    criteria: Optional[list[str]] = None


class GoalEvidenceRequest(BaseModel):
    session_id: str
    evidence: str
    source: str = "api"
    hypothesis_id: Optional[str] = None
    criterion_id: Optional[str] = None
    run_id: Optional[str] = None


class GoalCompleteRequest(BaseModel):
    session_id: str
    outcome: str = "complete"
    summary: Optional[str] = None


@router.post("/start")
async def goal_start(req: GoalStartRequest, request: Request):
    """创建新 research goal。"""
    try:
        from agentnodes.apps.goal import GoalStore, RiskTier
        from agentnodes.apps.goal.context import default_goal_criteria
        from agentnodes.apps.goal.events import (
            CHANGE_TYPE_CREATE,
        )

        db_path = getattr(request.app.state, "goal_db_path", None)
        criteria = req.criteria or default_goal_criteria()
        risk_tier = RiskTier(req.risk_tier)
        _session_owned(request, req.session_id)

        with GoalStore(db_path=db_path) as store:
            goal = store.replace_goal(
                session_id=req.session_id,
                objective=req.objective,
                criteria=criteria,
                risk_tier=risk_tier,
                user_id=getattr(request.state, "user_id", "anonymous"),
            )
            # Full-snapshot SSE so the chat panel + message stream
            # update immediately (same event as the chat-tool path).
            _emit_goal_updated(
                request, store, req.session_id, CHANGE_TYPE_CREATE,
            )
        return {"status": "ok", "goal_id": goal.goal_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def goal_status(session_id: str, request: Request):
    """获取当前 goal 状态（含 criteria / evidence_count / progress 完整快照）。"""
    try:
        from agentnodes.apps.goal import GoalStore

        _session_owned(request, session_id)
        db_path = getattr(request.app.state, "goal_db_path", None)
        with GoalStore(db_path=db_path) as store:
            snapshot = store.get_current_snapshot(session_id)
        if snapshot is None:
            return {"status": "no_goal", "session_id": session_id}

        goal = snapshot.get("goal", {})
        criteria = snapshot.get("criteria", [])
        return {
            "status": "ok",
            "goal_id": goal.get("goal_id"),
            "goal_status": goal.get("status"),
            "objective": goal.get("objective"),
            "progress_percent": goal.get("progress_percent", 0),
            "recap": goal.get("recap"),
            "session_id": session_id,
            "criteria": [
                {
                    "criterion_id": c.get("criterion_id"),
                    "text": c.get("text"),
                    "status": c.get("status"),
                    "required": c.get("required", True),
                }
                for c in criteria
            ],
            "criteria_count": len(criteria),
            "evidence_count": snapshot.get("evidence_count", 0),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def goal_list(
    request: Request,
    session_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
):
    """列出 goals。"""
    try:
        from agentnodes.apps.goal import GoalStatus, GoalStore

        db_path = getattr(request.app.state, "goal_db_path", None)
        status_filter = GoalStatus(status) if status else None
        with GoalStore(db_path=db_path) as store:
            goals = store.list_goals(
                session_id=session_id,
                status=status_filter,
                limit=limit,
                user_id=getattr(request.state, "user_id", "anonymous"),
            )
        return {
            "status": "ok",
            "goals": [
                {
                    "goal_id": g.goal_id,
                    "session_id": g.session_id,
                    "goal_status": g.status.value,
                    "objective": g.objective,
                    "workflow_id": g.workflow_id,
                    "created_at": g.created_at,
                }
                for g in goals
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/evidence")
async def goal_evidence(req: GoalEvidenceRequest, request: Request):
    """添加 evidence。"""
    try:
        from agentnodes.apps.goal import EvidenceInput, GoalStore
        from agentnodes.apps.goal.events import (
            CHANGE_TYPE_EVIDENCE,
        )

        db_path = getattr(request.app.state, "goal_db_path", None)
        _session_owned(request, req.session_id)
        evidence_input = EvidenceInput(
            text=req.evidence,
            source_type=req.source,
            run_id=req.run_id,
            criterion_id=req.criterion_id,
        )
        with GoalStore(db_path=db_path) as store:
            current = store.get_current_goal(req.session_id)
            if current is None:
                raise HTTPException(status_code=404, detail="No active goal for this session")
            evidence_record = store.append_evidence(
                session_id=req.session_id,
                goal_id=current.goal_id,
                expected_goal_id=current.goal_id,
                evidence=evidence_input,
            )
            _emit_goal_updated(
                request, store, req.session_id, CHANGE_TYPE_EVIDENCE,
                evidence_text=req.evidence,
            )
        return {
            "status": "ok",
            "goal_id": current.goal_id,
            "evidence_id": evidence_record.evidence_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/complete")
async def goal_complete(req: GoalCompleteRequest, request: Request):
    """完成 goal。"""
    from agentnodes.apps.goal import GoalStatus, GoalStore, StaleGoalError
    from agentnodes.apps.goal.events import CHANGE_TYPE_COMPLETE

    try:
        db_path = getattr(request.app.state, "goal_db_path", None)
        _session_owned(request, req.session_id)
        try:
            target_status = GoalStatus(req.outcome)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid outcome: {req.outcome}",
            )

        with GoalStore(db_path=db_path) as store:
            current = store.get_current_goal(req.session_id)
            if current is None:
                raise HTTPException(status_code=404, detail="No active goal for this session")
            updated = store.update_status(
                session_id=req.session_id,
                goal_id=current.goal_id,
                expected_goal_id=current.goal_id,
                status=target_status,
                recap=req.summary,
            )
            _emit_goal_updated(
                request, store, req.session_id, CHANGE_TYPE_COMPLETE,
            )
        return {
            "status": "ok",
            "goal_id": updated.goal_id,
            "new_status": updated.status.value,
        }
    except HTTPException:
        raise
    except StaleGoalError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _emit_goal_updated(
    request: Request,
    store,
    session_id: str,
    change_type: str,
    *,
    evidence_text: Optional[str] = None,
) -> None:
    """Emit the full-snapshot ``goal_updated`` event on the session bus.

    Best-effort: the mutation has already committed; a failure here
    only loses the live push (the projector / next loadSessionState
    will still surface the new state).
    """
    try:
        from agentnodes.apps.goal.events import build_goal_updated_payload

        from .chat import _get_session_service

        service = _get_session_service()
        bus = getattr(service, "event_bus", None)
        if bus is None:
            return
        payload = build_goal_updated_payload(
            session_id,
            store,
            change_type,
            evidence_text=evidence_text,
        )
        if payload is not None:
            bus.emit(session_id, "goal_updated", payload)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).debug(
            "goal SSE emit failed for %s", session_id, exc_info=True
        )
