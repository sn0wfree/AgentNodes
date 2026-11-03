"""Hypothesis API router — /api/hypothesis/*"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


def _user_goal_ids(request: Request) -> set[str] | None:
    """Goal ids the requesting user may access (session scoped).

    Resolution chain: user -> owned sessions -> goal rows on those
    sessions. Returns ``None`` when isolation is disabled (no filtering);
    otherwise the caller's allowed goal-id set (possibly empty, meaning
    the user owns no goals). Hypotheses without a goal_id are legacy
    orphans and are treated as visible to everyone.
    """
    import os as _os

    if _os.environ.get("SR_ENFORCE_STUDY_IDOR", "1") == "0":
        return None
    user_id = getattr(request.state, "user_id", None) or "anonymous"
    from strategy_research.apps.goal import GoalStore
    from .web_session import _get_db

    with _get_db() as conn:
        rows = conn.execute(
            "SELECT id FROM sessions WHERE user_id = ?", (user_id,)
        ).fetchall()
    session_ids = [r["id"] for r in rows]
    if not session_ids:
        return set()

    db_path = getattr(request.app.state, "goal_db_path", None)
    goal_ids: set[str] = set()
    with GoalStore(db_path=db_path) as store:
        for sid in session_ids:
            for g in store.list_goals(session_id=sid, limit=100000):
                goal_ids.add(g.goal_id)
    return goal_ids


class HypothesisCreateRequest(BaseModel):
    title: str
    thesis: str = ""
    status: str = "exploring"
    universe: str = ""
    signal_definition: str = ""


class HypothesisUpdateRequest(BaseModel):
    hypothesis_id: str
    status: Optional[str] = None
    title: Optional[str] = None
    thesis: Optional[str] = None
    conclusion: Optional[str] = None


@router.post("/create")
async def hypothesis_create(req: HypothesisCreateRequest, request: Request):
    """创建新 hypothesis。"""
    try:
        from pathlib import Path

        from strategy_research.apps.hypothesis import HypothesisRegistry

        hyp_path = getattr(request.app.state, "hypotheses_path", None)
        registry = HypothesisRegistry(path=Path(hyp_path) if hyp_path else None)
        hyp = registry.create(
            title=req.title,
            thesis=req.thesis,
            status=req.status,
            universe=req.universe,
            signal_definition=req.signal_definition,
        )
        return {"status": "ok", "hypothesis_id": hyp.hypothesis_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def hypothesis_list(
    request: Request,
    status: Optional[str] = None,
    limit: int = 50,
):
    """列出 hypotheses。"""
    try:
        from pathlib import Path

        from strategy_research.apps.hypothesis import HypothesisRegistry

        hyp_path = getattr(request.app.state, "hypotheses_path", None)
        registry = HypothesisRegistry(path=Path(hyp_path) if hyp_path else None)
        items = registry.list()
        if status:
            items = [h for h in items if h.status == status]
        allowed = _user_goal_ids(request)
        if allowed is not None:
            items = [h for h in items if not h.goal_id or h.goal_id in allowed]
        return {
            "status": "ok",
            "hypotheses": [h.to_dict() for h in items[:limit]],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search")
async def hypothesis_search(
    request: Request,
    q: str = "",
    limit: int = 20,
):
    """搜索 hypotheses。"""
    try:
        from pathlib import Path

        from strategy_research.apps.hypothesis import HypothesisRegistry

        hyp_path = getattr(request.app.state, "hypotheses_path", None)
        registry = HypothesisRegistry(path=Path(hyp_path) if hyp_path else None)
        results = registry.search(query=q)
        allowed = _user_goal_ids(request)
        if allowed is not None:
            results = [h for h in results if not h.goal_id or h.goal_id in allowed]
        return {
            "status": "ok",
            "results": [h.to_dict() for h in results[:limit]],
            "query": q,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{hypothesis_id}")
async def hypothesis_get(hypothesis_id: str, request: Request):
    """获取单个 hypothesis。"""
    try:
        from pathlib import Path

        from strategy_research.apps.hypothesis import HypothesisRegistry

        hyp_path = getattr(request.app.state, "hypotheses_path", None)
        registry = HypothesisRegistry(path=Path(hyp_path) if hyp_path else None)
        hyp = registry.get(hypothesis_id)
        if hyp is None:
            raise HTTPException(status_code=404, detail=f"Hypothesis {hypothesis_id} not found")
        return {"status": "ok", "hypothesis": hyp.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/update")
async def hypothesis_update(req: HypothesisUpdateRequest, request: Request):
    """更新 hypothesis。"""
    try:
        from pathlib import Path

        from strategy_research.apps.hypothesis import HypothesisRegistry

        hyp_path = getattr(request.app.state, "hypotheses_path", None)
        registry = HypothesisRegistry(path=Path(hyp_path) if hyp_path else None)
        updates = {
            k: v
            for k, v in req.dict().items()
            if v is not None and k != "hypothesis_id"
        }
        hyp = registry.update(req.hypothesis_id, **updates)
        if hyp is None:
            raise HTTPException(status_code=404, detail=f"Hypothesis {req.hypothesis_id} not found")
        return {"status": "ok", "hypothesis": hyp.to_dict()}
    except HTTPException:
        raise
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        msg = str(e).lower()
        code = 404 if "not found" in msg else 400
        raise HTTPException(status_code=code, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
