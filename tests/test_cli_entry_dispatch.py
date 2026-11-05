"""Entry-dispatch tests: TTY-aware routing + REPL/TUI/argparse paths.

The dispatcher lives in :mod:`agentnodes.interfaces.cli.__main__` and is the
binary entry point (``agentnodes`` after ``pip install -e .``).

Routing matrix tested here:

================================  ============================================
``[]`` (TTY)                       Textual TUI (``ResearchApp``).
``[]`` (non-TTY)                   argparse help.
``--banner`` (TTY)                 legacy prompt_toolkit REPL.
``--repl`` (TTY)                   legacy prompt_toolkit REPL.
``status``                         argparse subcommand.
``--help``                         argparse help.
``--llm-list-profiles``            argparse (subcommand list).
================================  ============================================

TTY handling
-----------
``_is_interactive_invocation`` accepts an ``is_tty`` callable so tests
inject a deterministic probe instead of relying on
``sys.stdin.isatty()`` (which conflicts with pytest's capture).
"""
from __future__ import annotations

from unittest import mock

import agentnodes.interfaces.cli.__main__ as entry
from agentnodes.interfaces.cli.__main__ import (
    _is_interactive_invocation,
    _wants_legacy_repl,
    main,
)

# ─── Helpers ──────────────────────────────────────────────────────────


def _true_tty():
    return True


def _false_tty():
    return False


# ─── Detection rule ──────────────────────────────────────────────────


class TestIsInteractiveInvocation:
    """Pure rules — TTY flag + flag recognition + subcommand handling."""

    def test_bare_argv_tty_returns_true(self):
        assert _is_interactive_invocation([], is_tty=_true_tty) is True

    def test_banner_argv_tty_returns_true(self):
        assert _is_interactive_invocation(["--banner"], is_tty=_true_tty) is True

    def test_repl_argv_tty_returns_true(self):
        assert _is_interactive_invocation(["--repl"], is_tty=_true_tty) is True

    def test_subcommand_returns_false(self):
        assert _is_interactive_invocation(["status"], is_tty=_true_tty) is False

    def test_subcommand_with_args_returns_false(self):
        assert _is_interactive_invocation(
            ["autoresearch", ".", "--max-rounds", "10"],
            is_tty=_true_tty,
        ) is False

    def test_help_returns_false(self):
        assert _is_interactive_invocation(["--help"], is_tty=_true_tty) is False

    def test_llm_list_profiles_returns_false(self):
        assert _is_interactive_invocation(
            ["--llm-list-profiles"], is_tty=_true_tty
        ) is False

    def test_unknown_flag_returns_false(self):
        assert _is_interactive_invocation(["--whatever"], is_tty=_true_tty) is False

    def test_tty_guard_blocks_bare_invocation(self):
        # Piped invocation should NOT enter interactive (would hang).
        assert _is_interactive_invocation([], is_tty=_false_tty) is False

    def test_tty_guard_blocks_banner(self):
        assert _is_interactive_invocation(["--banner"], is_tty=_false_tty) is False

    def test_tty_guard_blocks_repl(self):
        assert _is_interactive_invocation(["--repl"], is_tty=_false_tty) is False


class TestWantsLegacyRepl:
    def test_empty_argv_returns_false(self):
        assert _wants_legacy_repl([]) is False

    def test_repl_argv_returns_true(self):
        assert _wants_legacy_repl(["--repl"]) is True

    def test_banner_argv_returns_true(self):
        assert _wants_legacy_repl(["--banner"]) is True

    def test_subcommand_returns_false(self):
        assert _wants_legacy_repl(["status"]) is False


# ─── main() routing ──────────────────────────────────────────────────


class TestMainRouting:
    """End-to-end routing through ``main()``. Mock Textual and REPL entry
    points so we never actually start an event loop.
    """

    def test_bare_argv_tty_starts_tui(self):
        with mock.patch.object(
            entry, "ResearchApp",
            return_value=mock.MagicMock(run=lambda: 0),
        ) as cls:
            rc = main([], is_tty=_true_tty)
        cls.assert_called_once()
        assert rc == 0

    def test_bare_argv_no_tty_falls_back_to_argparse(self):
        with mock.patch.object(entry, "_cli_main", return_value=0) as m:
            rc = main([], is_tty=_false_tty)
        m.assert_called_once()
        assert rc == 0

    def test_repl_argv_tty_uses_legacy_repl(self):
        with mock.patch.object(entry, "_interactive_main", return_value=0) as m:
            rc = main(["--repl"], is_tty=_true_tty)
        m.assert_called_once_with(["--repl"])
        assert rc == 0

    def test_banner_argv_tty_uses_legacy_repl(self):
        with mock.patch.object(entry, "_interactive_main", return_value=0) as m:
            rc = main(["--banner"], is_tty=_true_tty)
        m.assert_called_once_with(["--banner"])
        assert rc == 0

    def test_subcommand_uses_argparse(self):
        with mock.patch.object(entry, "_cli_main", return_value=0) as m:
            rc = main(["status"], is_tty=_true_tty)
        m.assert_called_once()
        assert rc == 0

    def test_unknown_flag_uses_argparse(self):
        with mock.patch.object(entry, "_cli_main", return_value=0) as m:
            rc = main(["--unknown"], is_tty=_true_tty)
        m.assert_called_once()
        assert rc == 0

    def test_tui_failure_falls_back_to_repl(self):
        """If Textual raises during boot, we fall back to legacy REPL.

        Escape hatch for users whose terminal doesn't support modern
        features Textual needs (mouse, truecolor, etc.).
        """

        class _FailingApp:
            def __init__(self, *a, **kw):
                pass

            def run(self):
                raise RuntimeError("Textual init failed")

        with mock.patch.object(entry, "ResearchApp", _FailingApp):
            with mock.patch.object(entry, "_interactive_main", return_value=0) as m:
                rc = main([], is_tty=_true_tty)
        m.assert_called_once_with([])
        assert rc == 0

    def test_propagates_tui_return_code(self):
        class _OkApp:
            def __init__(self):
                pass

            def run(self):
                return 0

        with mock.patch.object(entry, "ResearchApp", _OkApp):
            assert main([], is_tty=_true_tty) == 0

    def test_argparse_path_restores_sys_argv(self):
        import sys as _sys
        original = list(_sys.argv)
        with mock.patch.object(entry, "_cli_main", return_value=0):
            main(["status", "--strategy", "my_strat"], is_tty=_true_tty)
        assert _sys.argv == original

    def test_tui_path_does_not_touch_sys_argv(self):
        import sys as _sys
        original = list(_sys.argv)

        class _OkApp:
            def __init__(self):
                pass

            def run(self):
                return 0

        with mock.patch.object(entry, "ResearchApp", _OkApp):
            main([], is_tty=_true_tty)
        assert _sys.argv == original


# ─── Imports sanity ─────────────────────────────────────────────────


class TestRealImports:
    def test_repl_main_importable(self):
        from agentnodes.interfaces.cli.interactive.main import main as repl
        assert callable(repl)

    def test_cli_main_importable(self):
        from agentnodes.interfaces.cli import main as cli
        assert callable(cli)

    def test_research_app_importable(self):
        from agentnodes.interfaces.cli.tui.app import ResearchApp
        assert callable(ResearchApp)

    def test_dispatcher_module_entries(self):
        from agentnodes.interfaces.cli.__main__ import main as m
        assert callable(m)
