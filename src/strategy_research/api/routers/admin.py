"""Admin API — emergency toggles, metrics, audit log.

Endpoints (require admin token via X-Admin-Token header):
- POST /api/admin/compaction/keep-all/{true|false}?confirm=yes
  Runtime kill switch for keep_all_compactions_in_history filter.
- GET /api/admin/compaction/metrics
  Read compaction metrics (hidden count, kept count, L4 aborts).
- GET /api/admin/audit-log
  Read recent admin actions.

Admin token: set SR_ADMIN_TOKEN env var. If unset, admin endpoints
return 503 (disabled). This is a safety mechanism to prevent
accidental admin actions.

Audit log: in-memory ring buffer of last 100 admin actions.
"""

from __future__ import annotations

import hmac
import logging
import os
import time
from collections import deque
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Admin token validation ────────────────────────────────────────────


def _get_admin_token() -> str:
    """Get the admin token from env. Returns empty string if disabled."""
    return os.environ.get("SR_ADMIN_TOKEN", "").strip()


def _verify_admin(x_admin_token: str | None = Header(None)) -> None:
    """Verify admin token. Raises 401/503 if invalid/disabled."""
    expected = _get_admin_token()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Admin endpoints disabled. Set SR_ADMIN_TOKEN env var.",
        )
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing X-Admin-Token header.",
        )


# ── Audit log (in-memory ring buffer) ────────────────────────────────


_audit_log: deque[dict[str, Any]] = deque(maxlen=100)


def _record_audit(action: str, details: dict[str, Any]) -> None:
    """Record an admin action to the audit log."""
    entry = {
        "timestamp": time.time(),
        "action": action,
        "details": details,
    }
    _audit_log.append(entry)
    logger.info("[ADMIN] %s %s", action, details)


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/compaction/keep-all/{enabled}")
async def toggle_keep_all_compactions(
    enabled: bool,
    confirm: str = Query("", description="Must be 'yes' to confirm"),
    x_admin_token: str | None = Header(None),
) -> dict[str, Any]:
    """Toggle keep_all_compactions_in_history filter at runtime.

    Args:
        enabled: true = force all compactions in LLM history (legacy);
                 false = filter to most recent only (new behavior).
        confirm: must be 'yes' to prevent accidental toggles.

    Returns:
        Status of the toggle.
    """
    _verify_admin(x_admin_token)
    if confirm.lower() != "yes":
        raise HTTPException(
            status_code=400,
            detail="Confirmation required: pass ?confirm=yes",
        )

    from strategy_research.core.agent.compact import set_keep_all_override

    set_keep_all_override(enabled)
    _record_audit(
        "compaction.keep_all.toggle",
        {"enabled": enabled, "previous": not enabled},
    )

    return {
        "status": "ok",
        "keep_all_compactions": enabled,
        "note": "Runtime toggle. Persist by setting in ~/.quantnodes/llm.json or SR_KEEP_ALL_COMPACTIONS env var.",
    }


@router.get("/compaction/metrics")
async def get_compaction_metrics(
    x_admin_token: str | None = Header(None),
) -> dict[str, Any]:
    """Get in-memory compaction metrics for monitoring."""
    _verify_admin(x_admin_token)
    from strategy_research.core.agent.compact import get_compaction_metrics

    metrics = get_compaction_metrics()
    return {
        "status": "ok",
        "metrics": metrics,
    }


@router.post("/compaction/metrics/reset")
async def reset_compaction_metrics(
    confirm: str = Query("", description="Must be 'yes' to confirm"),
    x_admin_token: str | None = Header(None),
) -> dict[str, Any]:
    """Reset compaction metrics to zero (for testing)."""
    _verify_admin(x_admin_token)
    if confirm.lower() != "yes":
        raise HTTPException(
            status_code=400,
            detail="Confirmation required: pass ?confirm=yes",
        )

    from strategy_research.core.agent.compact import reset_compaction_metrics

    reset_compaction_metrics()
    _record_audit("compaction.metrics.reset", {})
    return {"status": "ok"}


@router.get("/audit-log")
async def get_audit_log(
    limit: int = Query(50, ge=1, le=100),
    x_admin_token: str | None = Header(None),
) -> dict[str, Any]:
    """Get recent admin actions (max 100, FIFO)."""
    _verify_admin(x_admin_token)
    entries = list(_audit_log)[-limit:]
    return {
        "status": "ok",
        "count": len(entries),
        "entries": entries,
    }


@router.get("/metrics")
async def get_session_metrics(
    recent: int = Query(20, ge=1, le=200,
                        description="Number of recent WriteMetric rows to include"),
    x_admin_token: str | None = Header(None),
) -> dict[str, Any]:
    """Write-path health (B4 D3: legacy metrics logger retired).

    Previously surfaced ``core.session.db.MetricsLogger`` -- an in-memory ring
    tied to the legacy session stack that B4 retires. ``EventStore`` is now
    the single write path, so its self-report is the honest replacement:
    backend kind, degraded flag, cache hit/miss, evictions. The response
    shape changed with it; no frontend calls this endpoint (only a generated
    type existed).

    Kept as a route rather than deleted so operators with a bookmark get a
    real answer instead of a 404.
    """
    _verify_admin(x_admin_token)
    from strategy_research.core.agent.event_store import EventStore
    from strategy_research.core.agent.memory_manager import resolve_session_db_path

    # health_report() only reads in-process cache/subscriber state, so the
    # store is discarded immediately -- no connection is left open.
    health = EventStore(
        resolve_session_db_path(), flush_to_messages=False,
    ).health_report()
    return {"status": "ok", "health": health, "recent": []}


@router.get("/hangs/report")
async def get_hangs_report(
    hours: float = Query(24, ge=0.1, le=24 * 30,
                         description="Look-back window in hours"),
    limit: int = Query(50, ge=1, le=200),
    x_admin_token: str | None = Header(None),
) -> dict[str, Any]:
    """Aggregate hanging-protection events for the ops runbook.

    Events are recorded by each protection layer (LLM wall-clock
    timeout, backtest log stall, agent no_progress, circuit breaker
    open, watchdog interrupt) into the goals DB ``hanging_events``
    table. This endpoint reports how often each fired in the window —
    the C.2 daily-drive signal for threshold tuning.
    """
    _verify_admin(x_admin_token)
    from strategy_research.core.study.hanging_events import HangingEventsStore

    with HangingEventsStore() as store:
        rep = store.report(hours=hours, limit=limit)
    return {
        "status": "ok",
        "window_hours": hours,
        "report": rep,
    }


@router.get("/health")
async def admin_health() -> dict[str, Any]:
    """Admin health check (no auth required — just shows if admin is enabled)."""
    return {
        "admin_enabled": bool(_get_admin_token()),
    }
