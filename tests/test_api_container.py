"""Tests for api/container.py (production-wired DI container)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestBuildContainer(unittest.TestCase):

    def test_basic_build(self):
        from agentnodes.interfaces.api.container import build_container, close_container

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            container = build_container(
                workspace_path=Path(tmpdir),
                db_path=db_path,
            )
            # W1-8: 必须在临时目录清理前释放 SQLite 句柄
            # （Windows 拒绝删除仍被进程占用的文件，WinError 32）
            try:
                self.assertEqual(container.workspace_path, Path(tmpdir))
                self.assertEqual(container.db_path, db_path)
                self.assertIsNotNone(container.event_store)
                self.assertIsNotNone(container.session_store)
                self.assertIsNotNone(container.session_service)
                self.assertIs(container.event_store._projector, container.projector)
                self.assertIs(container.session_store._projector, container.projector)
                self.assertIs(container.memory_manager._projection_sink, container.projector)
            finally:
                close_container(container)

    def test_services_wired_correctly(self):
        """Services share the same EventStore instance."""
        from agentnodes.interfaces.api.container import build_container, close_container

        with TemporaryDirectory() as tmpdir:
            container = build_container(
                workspace_path=Path(tmpdir),
                db_path=Path(tmpdir) / "test.db",
            )
            try:
                # SessionService is wired with the EventStore
                self.assertIs(container.session_service.event_bus, container.event_store)
                # SessionStore should match db_path
                self.assertEqual(container.session_store.db_path, Path(tmpdir) / "test.db")
            finally:
                close_container(container)

    def test_default_db_path(self):
        """When db_path is not given, use the unified session DB resolver."""
        from agentnodes.interfaces.api.container import build_container, close_container

        with TemporaryDirectory() as tmpdir:
            import os

            os.environ["AGENTNODES_WORKSPACE"] = tmpdir
            container = None
            try:
                container = build_container()
                # 2026-11-09 路径统一：会话库文件名随 profile（默认
                # .agentnodes_session.db）。断言引用真源，不硬编码旧名。
                from agentnodes.foundation.paths import session_db_filename

                self.assertEqual(
                    container.db_path,
                    (Path(tmpdir) / session_db_filename()).resolve(),
                )
            finally:
                if container is not None:
                    close_container(container)
                del os.environ["AGENTNODES_WORKSPACE"]

    def test_no_workspace(self):
        """build_container works without workspace_path."""
        from agentnodes.interfaces.api.container import build_container, close_container

        container = build_container(db_path=Path("/tmp/x.db"))
        try:
            self.assertIsNone(container.workspace_path)
            self.assertEqual(container.db_path, Path("/tmp/x.db"))
        finally:
            close_container(container)


class TestBuildContainerWithFactories(unittest.TestCase):

    def test_event_store_factory_override(self):
        from agentnodes.interfaces.api.container import build_container

        fake_store = MagicMock(name="fake_event_store")
        fake_store._sse_bridge_attached = True  # Skip SSE attach
        container = build_container(
            db_path=Path("/tmp/x.db"),
            event_store_factory=lambda db_path: fake_store,
        )
        try:
            self.assertIs(container.event_store, fake_store)
        finally:
            from agentnodes.interfaces.api.container import close_container

            close_container(container)

    def test_session_service_factory_override(self):
        from agentnodes.interfaces.api.container import build_container

        fake_svc = MagicMock(name="fake_session_service")
        container = build_container(
            db_path=Path("/tmp/x.db"),
            session_service_factory=lambda store, bus: fake_svc,
        )
        try:
            self.assertIs(container.session_service, fake_svc)
        finally:
            from agentnodes.interfaces.api.container import close_container

            close_container(container)


class TestAttachContainer(unittest.TestCase):

    def test_attach_and_get(self):
        from agentnodes.interfaces.api.container import (
            attach_container,
            build_container,
            get_container,
        )

        # Fake app with .state
        class FakeApp:
            pass

        app = FakeApp()
        app.state = type("State", (), {})()

        container = build_container(db_path=Path("/tmp/x.db"))
        try:
            attach_container(app, container)

            # get_container works
            request = MagicMock()
            request.app = app
            result = get_container(request)
            self.assertIs(result, container)
        finally:
            from agentnodes.interfaces.api.container import close_container

            close_container(container)

    def test_get_container_without_attach_raises(self):
        from agentnodes.interfaces.api.container import get_container

        class FakeApp:
            pass

        app = FakeApp()
        app.state = type("State", (), {})()

        request = MagicMock()
        request.app = app

        with self.assertRaises(RuntimeError):
            get_container(request)


class TestResetContainer(unittest.TestCase):

    def test_reset_detaches(self):
        from agentnodes.interfaces.api.container import (
            attach_container,
            build_container,
            reset_container,
        )

        class FakeApp:
            pass

        app = FakeApp()
        app.state = type("State", (), {})()

        container = build_container(db_path=Path("/tmp/x.db"))
        try:
            attach_container(app, container)
            self.assertTrue(hasattr(app.state, "_container"))

            reset_container(app)
            self.assertFalse(hasattr(app.state, "_container"))
        finally:
            from agentnodes.interfaces.api.container import close_container

            close_container(container)


class TestServicesFromContainer(unittest.TestCase):

    def test_extracts_all_services(self):
        from agentnodes.interfaces.api.container import (
            build_container,
            services_from_container,
        )

        container = build_container(db_path=Path("/tmp/x.db"))
        try:
            services = services_from_container(container)
            self.assertIn("_event_store", services)
            self.assertIn("_session_store", services)
            self.assertIn("_session_service", services)
            self.assertIn("_memory_manager", services)
            # Same instances
            self.assertIs(services["_event_store"], container.event_store)
            self.assertIs(services["_session_store"], container.session_store)
            self.assertIs(services["_session_service"], container.session_service)
        finally:
            from agentnodes.interfaces.api.container import close_container

            close_container(container)


class TestContainerIsFrozen(unittest.TestCase):

    def test_immutable(self):
        """AppContainer is a frozen dataclass — mutation raises."""
        from agentnodes.interfaces.api.container import build_container

        container = build_container(db_path=Path("/tmp/x.db"))
        try:
            with self.assertRaises(Exception):
                container.db_path = Path("/tmp/other.db")  # type: ignore[misc]
        finally:
            from agentnodes.interfaces.api.container import close_container

            close_container(container)


if __name__ == "__main__":
    unittest.main()
