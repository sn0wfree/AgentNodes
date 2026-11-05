"""Integration tests for the BaseTool permission gate.

Exercises ``BaseTool.ainvoke`` against the permission ruleset:
- ALLOW short-circuits (no gateway call).
- DENY raises ``PermissionDeniedError`` synchronously.
- ASK blocks on the gateway; ``respond`` resumes.
- ainvoke without ctx / gateway falls back to ``invoke`` (sync).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from agentnodes.kernel.agent.tools import BaseTool
from agentnodes.kernel.permission import (
    PermissionAction,
    PermissionGateway,
    PermissionResponse,
)

# ── Test double ───────────────────────────────────────────────────


class _EchoTool(BaseTool):
    """Minimal tool that echoes its kwargs so we can detect whether
    the gate let execution through."""

    name = "echo_tool"
    description = "Test echo"
    parameters = {"type": "object", "properties": {}, "additionalProperties": True}
    effects = frozenset({"EFFECT_FS"})  # NOT R0 — triggers the gate

    def execute(self, ctx, **kwargs):
        return f"ok:{kwargs.get('msg', '')}"


@dataclass
class _Ctx:
    workspace: Any = None
    session_id: str | None = "s1"
    emit_progress: Any = None
    permission_evaluator: Any = None
    permission_gateway: Any = None
    tool_call_id: str | None = None


@pytest.fixture
def gateway(tmp_path):
    return PermissionGateway(rules_path=tmp_path / "perm.yaml", timeout_s=1.0)


# ── ALLOW path ─────────────────────────────────────────────────────


def test_allow_short_circuits(gateway: PermissionGateway):
    tool = _EchoTool()
    # Force an ALLOW verdict via an explicit rule.
    gateway.evaluator.add_rule(  # type: ignore[attr-defined]
        type("R", (), {
            "permission": "echo_tool", "pattern": "*",
            "action": PermissionAction.ALLOW,
        })(),
    )
    ctx = _Ctx(
        permission_evaluator=gateway.evaluator,
        permission_gateway=gateway,
        tool_call_id="tc-allow",
    )
    result = asyncio.run(tool.ainvoke({"msg": "hi"}, ctx=ctx))
    assert result == "ok:hi"


# ── DENY path ──────────────────────────────────────────────────────


def test_deny_raises(gateway: PermissionGateway):
    tool = _EchoTool()
    # Add a deny rule.
    from agentnodes.kernel.permission.schema import PermissionRule
    gateway.evaluator.add_rule(PermissionRule(
        permission="echo_tool", pattern="*",
        action=PermissionAction.DENY,
    ))
    ctx = _Ctx(
        permission_evaluator=gateway.evaluator,
        permission_gateway=gateway,
        tool_call_id="tc-deny",
    )
    with pytest.raises(Exception) as ei:
        asyncio.run(tool.ainvoke({"msg": "x"}, ctx=ctx))
    # PermissionDeniedError has a to_payload method.
    assert hasattr(ei.value, "to_payload") or "Permission denied" in str(ei.value)


# ── ASK path: user rejects ────────────────────────────────────────


def test_ask_then_deny(gateway: PermissionGateway):
    tool = _EchoTool()
    ctx = _Ctx(
        permission_evaluator=gateway.evaluator,
        permission_gateway=gateway,
        tool_call_id="tc-reject",
    )

    async def run():
        task = asyncio.create_task(
            tool.ainvoke({"msg": "x"}, ctx=ctx),
        )
        await asyncio.sleep(0)
        gateway.respond(
            "tc-reject",
            PermissionResponse(action=PermissionAction.DENY, reason="nope"),
        )
        return await task

    with pytest.raises(Exception) as ei:
        asyncio.run(run())
    assert "nope" in str(ei.value) or "Permission" in str(ei.value)


# ── ASK path: user allows ──────────────────────────────────────────


def test_ask_then_allow(gateway: PermissionGateway):
    tool = _EchoTool()
    ctx = _Ctx(
        permission_evaluator=gateway.evaluator,
        permission_gateway=gateway,
        tool_call_id="tc-allow-ask",
    )

    async def run():
        task = asyncio.create_task(
            tool.ainvoke({"msg": "hello"}, ctx=ctx),
        )
        await asyncio.sleep(0)
        gateway.respond(
            "tc-allow-ask",
            PermissionResponse(action=PermissionAction.ALLOW),
        )
        return await task

    result = asyncio.run(run())
    assert result == "ok:hello"


# ── Sync fallback: no ctx, no gateway ──────────────────────────────


def test_ainvoke_without_ctx_runs_sync(gateway: PermissionGateway):
    """The legacy ``invoke`` path stays untouched: ainvoke with
    ctx=None just delegates to invoke."""
    tool = _EchoTool()
    result = asyncio.run(tool.ainvoke({"msg": "sync"}))
    assert result == "ok:sync"


# ── ASK without tool_call_id falls open (logged warning) ───────────


def test_ask_without_tool_call_id_falls_open(gateway: PermissionGateway, caplog):
    tool = _EchoTool()
    ctx = _Ctx(
        permission_evaluator=gateway.evaluator,
        permission_gateway=gateway,
        tool_call_id=None,  # missing key
    )
    result = asyncio.run(tool.ainvoke({"msg": "open"}, ctx=ctx))
    # Falls open to allow (with a warning) instead of blocking.
    assert result == "ok:open"
