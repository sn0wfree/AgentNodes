"""Web search API endpoints.

Exposes MiniMax Token Plan search to the frontend so any page
(study, chat, etc.) can call it without re-implementing the
HTTP/auth logic. The agent-side ``websearch`` tool continues to
use the same backend via ``web_search()``.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query

from strategy_research.foundation.web.minimax_search import (
    has_minimax_credentials,
    minimax_search,
)

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("/minimax")
async def search_minimax(
    q: str = Query(..., min_length=1, max_length=2048, description="Search query"),
    count: int = Query(5, ge=1, le=10, description="Number of results (1-10)"),
):
    """Run a MiniMax Token Plan web search.

    Returns the raw payload from ``minimax_search()`` (a JSON-decoded
    dict). The frontend ``SearchPanel`` component consumes this directly
    without parsing strings. Returns HTTP 503 when the backend is
    not configured, 502 on upstream HTTP errors.
    """
    if not has_minimax_credentials():
        raise HTTPException(
            status_code=503,
            detail=(
                "MiniMax search is not configured on the server. "
                "Set MINIMAX_CODE_PLAN_KEY (or MINIMAX_API_KEY) "
                "in the environment and restart."
            ),
        )
    raw = minimax_search(q, count=count)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"bad backend payload: {exc}")
    if payload.get("status") != "ok":
        # Upstream returned an error envelope — surface as 502
        raise HTTPException(
            status_code=502,
            detail=payload.get("error", "minimax_search failed"),
        )
    return payload


@router.get("/minimax/health")
async def search_minimax_health():
    """Lightweight probe — returns whether MiniMax credentials are set.

    The frontend uses this to decide whether to show the search panel
    as enabled or greyed out.
    """
    return {
        "status": "ok",
        "configured": has_minimax_credentials(),
    }
