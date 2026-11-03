"""POST /api/chat/permission/respond — front-end answer to a
``permission_request`` SSE event.

The PermissionGateway (one per process, held by chat router) owns
the in-flight asyncio.Future keyed by ``tool_call_id``. This router
just relays the user's choice back to the gateway.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


class PermissionResponseRequest(BaseModel):
    tool_call_id: str = Field(..., min_length=1)
    action: str = Field(..., description="'allow' | 'deny'")
    permanent: bool = Field(
        default=False,
        description="True when the user picked 'always' / 'always reject'.",
    )
    reason: str = Field(default="", description="Optional user-supplied note.")


@router.post("/respond")
async def respond_permission(
    body: PermissionResponseRequest,
    request: Request,
) -> dict[str, str]:
    """Resolve a pending permission handshake.

    The user-facing UI (PermissionRequestDialog) POSTs this when the
    user clicks one of {Allow once, Allow always, Reject, Reject
    always}. The gateway translates the verdict into a tool-side
    outcome:

    * allow (one-shot or always) -> tool executes
    * deny (one-shot or always) -> tool errors out with the user's
      reason; agent loop surfaces the denial as a tool error
    """
    from strategy_research.kernel.permission import PermissionAction, PermissionResponse
    from .chat import _get_permission_gateway

    gateway = _get_permission_gateway(request)
    if gateway is None:
        # Gateway not wired up (e.g. test fixtures, sync-only
        # builds). Treat as no-op so the UI doesn't loop on retries.
        logger.warning("permission respond: gateway not available")
        return {"status": "no_gateway"}

    # P5-5①: ownership — a permission answer mutates the process-wide
    # ruleset (permanent=true persists rules), so only the session
    # owner may answer. Unknown/expired ids fall through to the
    # gateway's expired path (no oracle).
    user_id = getattr(request.state, "user_id", None) or "anonymous"
    owner_session = gateway.owner_session_of(body.tool_call_id)
    if owner_session:
        from .web_session import _fetch_session_owned, _get_db
        _fetch_session_owned(_get_db(), owner_session, user_id)

    try:
        action_enum = PermissionAction(body.action.lower())
    except ValueError:
        logger.warning("permission respond: invalid action=%s", body.action)
        return {"status": "invalid_action"}

    response = PermissionResponse(
        action=action_enum,
        permanent=body.permanent,
        reason=body.reason,
    )
    resolved = gateway.respond(body.tool_call_id, response)
    return {"status": "ok" if resolved else "expired"}
