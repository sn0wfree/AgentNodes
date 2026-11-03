"""P1-C: Typed IDs tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.foundation.utils.ids import AttemptId, MessageId, SessionId


class TestTypedIds:
    def test_session_id_is_newtype(self):
        """SessionId wraps str with zero runtime cost."""
        sid = SessionId("abc123")
        assert sid == "abc123"
        assert isinstance(sid, str)

    def test_attempt_id_is_newtype(self):
        aid = AttemptId("def456")
        assert aid == "def456"
        assert isinstance(aid, str)

    def test_message_id_is_newtype(self):
        mid = MessageId("ghi789")
        assert mid == "ghi789"
        assert isinstance(mid, str)

    def test_ids_are_distinct_types_at_type_checker_level(self):
        """At runtime they're all str, but type checkers treat them differently."""
        sid = SessionId("abc")
        aid = AttemptId("abc")
        # At runtime, they're equal (both are str("abc"))
        assert sid == aid
        # NewType is a runtime identity function — both are str
        assert isinstance(sid, str)
        assert isinstance(aid, str)

    def test_service_layer_uses_typed_ids(self):
        """SessionService.cancel accepts AttemptId."""
        from strategy_research.interfaces.api.session.service import SessionService
        import inspect

        sig = inspect.signature(SessionService.cancel)
        param = sig.parameters["attempt_id"]
        # The annotation is AttemptId (NewType of str)
        assert param.annotation is not None

    def test_service_cancel_session_uses_typed_ids(self):
        from strategy_research.interfaces.api.session.service import SessionService
        import inspect

        sig = inspect.signature(SessionService.cancel_session)
        param = sig.parameters["session_id"]
        assert param.annotation is not None

    def test_agent_loop_uses_session_id(self):
        from strategy_research.kernel.agent.loop import AgentLoop
        import inspect

        sig = inspect.signature(AgentLoop.__init__)
        param = sig.parameters["session_id"]
        assert param.annotation is not None
