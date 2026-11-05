"""Tests for cli.commands.registry + core_commands (Phase 2.2)."""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.interfaces.cli.commands import core_commands  # noqa: F401  (registration side effect)
from strategy_research.interfaces.cli.commands.registry import (
    cli_command,
    dispatch,
    register_command,
    registered_commands,
    reset_registry,
    wire_commands,
)


def _reload_core_commands() -> None:
    """Re-import core_commands so its ``cli_command`` registrations re-run.

    Several tests call ``reset_registry()`` (clearing the global command
    table) without re-registering core commands afterwards; without this
    hook every later test that dispatches a CLI command (e.g. ``init`` in
    test_init_e2e) sees an empty registry.
    """
    import importlib

    importlib.reload(core_commands)


def setup_module() -> None:
    _reload_core_commands()


def teardown_module() -> None:
    _reload_core_commands()


class TestRegistryBasics(unittest.TestCase):

    def setUp(self):
        reset_registry()
        # Re-import to repopulate the global registry after reset.
        # Since core_commands registers on import, we have to re-import.
        import importlib

        import strategy_research.interfaces.cli.commands.core_commands as cc
        importlib.reload(cc)

    def test_registered_commands_returns_list(self):
        cmds = registered_commands()
        self.assertIsInstance(cmds, list)
        self.assertIn("init", cmds)
        # autoresearch 已随 apps/autoresearch 移除（2026-11-05 批次 3）
        self.assertNotIn("autoresearch", cmds)

    def test_reset_clears(self):
        len(registered_commands())
        reset_registry()
        self.assertEqual(len(registered_commands()), 0)
        # Re-register
        import importlib

        import strategy_research.interfaces.cli.commands.core_commands as cc
        importlib.reload(cc)
        self.assertGreater(len(registered_commands()), 0)

    def test_commands_are_sorted(self):
        cmds = registered_commands()
        self.assertEqual(cmds, sorted(cmds))


class TestCliCommandDecorator(unittest.TestCase):

    def setUp(self):
        reset_registry()

    def test_decorator_registers_handler(self):
        @cli_command("test_cmd", help="test help")
        def cmd_test(args):
            return 0

        self.assertIn("test_cmd", registered_commands())
        # Invoke directly
        ns = argparse.Namespace(command="test_cmd", handler=cmd_test)
        self.assertEqual(dispatch(ns), 0)

    def test_decorator_with_add_callable(self):
        @cli_command(
            "with_args",
            help="with args",
            add=lambda p: p.add_argument("--name", default="world"),
        )
        def cmd_with_args(args):
            return f"hello {args.name}"

        parser = argparse.ArgumentParser(prog="t")
        sub = parser.add_subparsers(dest="command")
        wire_commands(sub)

        # Parse with custom arg
        args = parser.parse_args(["with_args", "--name", "alice"])
        self.assertEqual(args.name, "alice")
        self.assertEqual(args.handler(args), "hello alice")

    def test_decorator_with_parents(self):
        parent = argparse.ArgumentParser(add_help=False)
        parent.add_argument("--shared", default="X")

        @cli_command("child", help="child", parents=[parent])
        def cmd_child(args):
            return args.shared

        parser = argparse.ArgumentParser(prog="t")
        sub = parser.add_subparsers(dest="command")
        wire_commands(sub)

        args = parser.parse_args(["child", "--shared", "Y"])
        self.assertEqual(args.handler(args), "Y")


class TestRegisterCommandManual(unittest.TestCase):

    def setUp(self):
        reset_registry()

    def test_manual_registration(self):
        def register(subparsers):
            p = subparsers.add_parser("manual", help="manual")
            p.set_defaults(handler=lambda a: 42)

        def handler(args):
            return 42

        register_command("manual", register, handler)
        self.assertIn("manual", registered_commands())

        parser = argparse.ArgumentParser(prog="t")
        sub = parser.add_subparsers(dest="command")
        wire_commands(sub)

        args = parser.parse_args(["manual"])
        self.assertEqual(args.handler(args), 42)


class TestWireCommands(unittest.TestCase):

    def setUp(self):
        reset_registry()
        import importlib

        import strategy_research.interfaces.cli.commands.core_commands as cc
        importlib.reload(cc)

    def test_wire_all_registered(self):
        parser = argparse.ArgumentParser(prog="t")
        sub = parser.add_subparsers(dest="command")
        wire_commands(sub)
        # Verify each registered command was added to the parser
        for cmd in registered_commands():
            try:
                # `import` requires --strategy + --source; everything else works bare
                if cmd == "import":
                    args = parser.parse_args([cmd, "--strategy", "s1", "--source", "csv"])
                else:
                    args = parser.parse_args([cmd])
                self.assertTrue(hasattr(args, "handler"))
            except SystemExit:
                self.fail(f"Command {cmd!r} not wired into parser")

    def test_extra_registrars_called(self):
        called = []

        def extra(subparsers):
            called.append("extra")

        parser = argparse.ArgumentParser(prog="t")
        sub = parser.add_subparsers(dest="command")
        wire_commands(sub, extra_registrars=[extra])
        self.assertEqual(called, ["extra"])

    def test_extra_registrar_exception_isolated(self):
        """A failing registrar should not break the whole wiring."""
        def bad(subparsers):
            raise RuntimeError("boom")

        parser = argparse.ArgumentParser(prog="t")
        sub = parser.add_subparsers(dest="command")
        # Should not raise — error is logged and swallowed.
        wire_commands(sub, extra_registrars=[bad])


class TestDispatch(unittest.TestCase):

    def setUp(self):
        reset_registry()

    def test_dispatch_runs_handler(self):
        @cli_command("echo", help="echo")
        def cmd_echo(args):
            return 7

        ns = argparse.Namespace(command="echo", handler=cmd_echo)
        self.assertEqual(dispatch(ns), 7)

    def test_dispatch_no_handler_prints_help(self):
        parser = argparse.ArgumentParser(prog="t")
        ns = argparse.Namespace(command="?")  # no .handler attribute
        # Should return 0, not crash
        result = dispatch(ns, parser=parser)
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
