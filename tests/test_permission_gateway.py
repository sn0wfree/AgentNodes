"""Async tests for the permission handshake gateway.

Tier 1 A1 — covers the asyncio.Future-based request/response pairing
between the backend tool execution and the front-end dialog.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentnodes.kernel.permission import (
    PermissionAction,
    PermissionGateway,
    PermissionResponse,
)


@pytest.fixture
def fast_timeout_gateway(tmp_path: Path) -> PermissionGateway:
    """Gateway with a 200 ms timeout so the timeout test is fast."""
    return PermissionGateway(rules_path=tmp_path / "perm.yaml", timeout_s=0.2)


def test_request_blocks_until_respond(fast_timeout_gateway: PermissionGateway):
    async def run():
        # Start the request in a background task.
        task = asyncio.create_task(
            fast_timeout_gateway.request(
                tool_call_id="tc-1",
                tool_name="write",
                args={"path": "x.py", "__session_id__": "s1"},
                decision=fast_timeout_gateway.evaluator.evaluate(
                    "write", {"path": "x.py"},
                ),
            ),
        )
        # Give the task a moment to register.
        await asyncio.sleep(0)
        assert "tc-1" in fast_timeout_gateway.pending

        ok = fast_timeout_gateway.respond(
            "tc-1",
            PermissionResponse(action=PermissionAction.ALLOW, permanent=False),
        )
        assert ok is True

        response = await task
        assert response.action == PermissionAction.ALLOW
        return response

    response = asyncio.run(run())
    assert response.action == PermissionAction.ALLOW


def test_respond_for_unknown_id_is_noop(fast_timeout_gateway: PermissionGateway):
    assert fast_timeout_gateway.respond(
        "ghost", PermissionResponse(action=PermissionAction.ALLOW),
    ) is False


def test_request_timeout_returns_deny(fast_timeout_gateway: PermissionGateway):
    async def run():
        response = await fast_timeout_gateway.request(
            tool_call_id="tc-timeout",
            tool_name="write",
            args={"path": "x.py"},
            decision=fast_timeout_gateway.evaluator.evaluate(
                "write", {"path": "x.py"},
            ),
        )
        return response

    response = asyncio.run(run())
    assert response.action == PermissionAction.DENY
    assert "timeout" in response.reason.lower()


def test_respond_twice_for_same_id_raises(fast_timeout_gateway: PermissionGateway):
    async def run():
        task = asyncio.create_task(
            fast_timeout_gateway.request(
                tool_call_id="tc-dup",
                tool_name="write",
                args={"path": "x.py"},
                decision=fast_timeout_gateway.evaluator.evaluate(
                    "write", {"path": "x.py"},
                ),
            ),
        )
        await asyncio.sleep(0)
        # First respond succeeds.
        assert fast_timeout_gateway.respond(
            "tc-dup",
            PermissionResponse(action=PermissionAction.ALLOW),
        )
        # Second respond is a no-op (the future is already done).
        assert fast_timeout_gateway.respond(
            "tc-dup",
            PermissionResponse(action=PermissionAction.ALLOW),
        ) is False
        await task

    asyncio.run(run())


def test_request_emits_hook_callback():
    """The on_request hook fires with the decision + args so the
    SSE push can build the dialog payload."""
    captured: list[tuple[str, str, dict]] = []

    def hook(tool_call_id: str, decision, args):
        captured.append((tool_call_id, decision.pattern, args))

    gw = PermissionGateway(
        rules_path=Path("/tmp/nonexistent-perm.yaml"),
        on_request=hook,
        timeout_s=1.0,
    )

    async def run():
        task = asyncio.create_task(
            gw.request(
                tool_call_id="tc-hook",
                tool_name="write",
                args={"path": "x.py"},
                decision=gw.evaluator.evaluate("write", {"path": "x.py"}),
            ),
        )
        await asyncio.sleep(0)
        gw.respond(
            "tc-hook",
            PermissionResponse(action=PermissionAction.ALLOW),
        )
        await task

    asyncio.run(run())
    assert len(captured) == 1
    tool_call_id, pattern, args = captured[0]
    assert tool_call_id == "tc-hook"
    assert pattern == "*"
    assert args["path"] == "x.py"


def test_request_persists_permanent_allow_rule(tmp_path: Path):
    """permanent=True ALLOW writes an allow rule that the evaluator
    picks up on subsequent calls in the same session."""
    path = tmp_path / "perm.yaml"
    gw = PermissionGateway(rules_path=path, timeout_s=1.0)

    async def run():
        # write_file defaults to ASK — confirm before the handshake.
        decision = gw.evaluator.evaluate("write", {"path": "y.py"})
        assert decision.action == PermissionAction.ASK

        task = asyncio.create_task(
            gw.request(
                tool_call_id="tc-perm",
                tool_name="write",
                args={"path": "y.py"},
                decision=decision,
            ),
        )
        await asyncio.sleep(0)
        gw.respond(
            "tc-perm",
            PermissionResponse(action=PermissionAction.ALLOW, permanent=True),
        )
        return await task

    response = asyncio.run(run())
    assert response.permanent is True

    # The rule file should now contain the appended rule.
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "permission: write_file" in text
    assert "action: allow" in text

    # And the evaluator should now allow the same pattern directly.
    follow_up = gw.evaluator.evaluate("write", {"path": "y.py"})
    assert follow_up.action == PermissionAction.ALLOW


def test_request_persists_permanent_deny_rule(tmp_path: Path):
    path = tmp_path / "perm.yaml"
    gw = PermissionGateway(rules_path=path, timeout_s=1.0)

    async def run():
        decision = gw.evaluator.evaluate("write", {"path": "danger.py"})
        task = asyncio.create_task(
            gw.request(
                tool_call_id="tc-deny",
                tool_name="write",
                args={"path": "danger.py"},
                decision=decision,
            ),
        )
        await asyncio.sleep(0)
        gw.respond(
            "tc-deny",
            PermissionResponse(
                action=PermissionAction.DENY,
                permanent=True,
                reason="user said no",
            ),
        )
        return await task

    asyncio.run(run())

    follow_up = gw.evaluator.evaluate("write", {"path": "danger.py"})
    assert follow_up.action == PermissionAction.DENY


def test_history_records_every_handshake(tmp_path: Path):
    gw = PermissionGateway(rules_path=tmp_path / "perm.yaml", timeout_s=1.0)

    async def run():
        for i in range(3):
            task = asyncio.create_task(
                gw.request(
                    tool_call_id=f"tc-{i}",
                    tool_name="write",
                    args={"path": f"f{i}.py"},
                    decision=gw.evaluator.evaluate(
                        "write", {"path": f"f{i}.py"},
                    ),
                ),
            )
            await asyncio.sleep(0)
            gw.respond(
                f"tc-{i}",
                PermissionResponse(action=PermissionAction.ALLOW),
            )
            await task

    asyncio.run(run())
    history = gw.history
    assert len(history) == 3
    assert [h["tool_call_id"] for h in history] == ["tc-0", "tc-1", "tc-2"]
    assert all(h["response_action"] == "allow" for h in history)
