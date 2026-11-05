"""Tests for api/dependencies.py (container-backed providers)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestDbPath(unittest.TestCase):

    def test_default_db_path(self):
        from agentnodes.interfaces.api.dependencies import _resolve_db_path

        old = os.environ.pop("AN_WORKSPACE_PATH", None)
        try:
            path = _resolve_db_path()
            self.assertTrue(str(path).endswith(".quantnodes_agentnodes_session.db"))
        finally:
            if old is not None:
                os.environ["AN_WORKSPACE_PATH"] = old

    def test_db_path_from_env(self):
        from agentnodes.interfaces.api.dependencies import _resolve_db_path

        with TemporaryDirectory() as tmpdir:
            os.environ["AN_WORKSPACE_PATH"] = tmpdir
            try:
                path = _resolve_db_path()
                self.assertEqual(path.parent, Path(tmpdir))
            finally:
                del os.environ["AN_WORKSPACE_PATH"]

    def test_get_db_path_from_workspace(self):
        from agentnodes.interfaces.api.dependencies import get_db_path

        with TemporaryDirectory() as tmpdir:
            state = type("State", (), {})()
            state.workspace_path = Path(tmpdir)
            request = MagicMock()
            request.app.state = state
            path = get_db_path(request)
            self.assertEqual(path.parent, Path(tmpdir))


class TestGetEventStore(unittest.TestCase):

    def test_caches_on_app_state(self):
        """Calling get_event_store twice should return the same instance."""
        from agentnodes.interfaces.api.dependencies import get_event_store

        request = MagicMock()
        request.app.state = type("State", (), {})()

        store1 = get_event_store(request)
        store2 = get_event_store(request)
        self.assertIs(store1, store2)

    def test_container_wins(self):
        """An attached container's EventStore is used."""
        from agentnodes.interfaces.api.container import build_container
        from agentnodes.interfaces.api.dependencies import get_event_store

        container = build_container(db_path=Path("/tmp/deps_test.db"))
        request = MagicMock()
        request.app.state = type("State", (), {})()
        request.app.state._container = container
        request.app.state._event_store = MagicMock(name="stale")

        store = get_event_store(request)
        self.assertIs(store, container.event_store)


class TestGetSessionService(unittest.TestCase):

    def test_caches(self):
        from agentnodes.interfaces.api.dependencies import get_session_service

        request = MagicMock()
        request.app.state = type("State", (), {})()

        svc1 = get_session_service(request)
        svc2 = get_session_service(request)
        self.assertIs(svc1, svc2)

    def test_container_wins(self):
        """An attached container's SessionService is the canonical one."""
        from agentnodes.interfaces.api.container import build_container
        from agentnodes.interfaces.api.dependencies import get_session_service

        container = build_container(db_path=Path("/tmp/deps_svc.db"))
        request = MagicMock()
        request.app.state = type("State", (), {})()
        request.app.state._container = container
        request.app.state._session_service = MagicMock(name="stale")

        svc = get_session_service(request)
        self.assertIs(svc, container.session_service)

    def test_state_override(self):
        """app.state._session_service can swap the service (test seam)."""
        from agentnodes.interfaces.api.dependencies import get_session_service

        request = MagicMock()
        request.app.state = type("State", (), {})()
        original = get_session_service(request)

        fake = MagicMock(name="fake_service")
        request.app.state._session_service = fake
        result = get_session_service(request)
        self.assertIs(result, fake)
        self.assertIsNot(result, original)


class TestResetAppState(unittest.TestCase):

    def test_reset_clears_state(self):
        from agentnodes.interfaces.api.dependencies import (
            get_event_store,
            get_session_service,
            reset_app_state,
        )

        class FakeApp:
            pass

        app = FakeApp()
        app.state = type("State", (), {})()

        request = MagicMock()
        request.app = app
        request.app.state = app.state

        get_event_store(request)
        get_session_service(request)

        self.assertTrue(hasattr(app.state, "_event_store"))
        self.assertTrue(hasattr(app.state, "_session_service"))

        reset_app_state(app)

        self.assertFalse(hasattr(app.state, "_event_store"))
        self.assertFalse(hasattr(app.state, "_session_service"))


class TestDependencyIntegration(unittest.TestCase):

    def test_services_share_event_store(self):
        """get_session_service should use the cached EventStore."""
        from agentnodes.interfaces.api.dependencies import (
            get_event_store,
            get_session_service,
        )

        request = MagicMock()
        request.app.state = type("State", (), {})()

        event_store = get_event_store(request)
        svc = get_session_service(request)
        # The service was constructed with the event_store instance
        self.assertIs(svc.event_bus, event_store)


if __name__ == "__main__":
    unittest.main()
