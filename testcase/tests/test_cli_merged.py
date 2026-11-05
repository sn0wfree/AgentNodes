"""W2 merged CLI surface: serve / backtest / check."""
from __future__ import annotations

import argparse

from strategy_research.interfaces.cli import _build_parser
from strategy_research.interfaces.cli.commands.merged import (
    add_backtest_parser,
    add_check_parser,
    dispatch_backtest,
    dispatch_check,
)


def _subcommands(parser: argparse.ArgumentParser) -> list[str]:
    for action in parser._actions:
        if action.dest == "command" and hasattr(action, "choices"):
            return sorted(action.choices)
    return []


class TestMergedCommandsPresent:
    def test_build_includes_merged_groups(self):
        parser, _ = _build_parser()
        names = set(_subcommands(parser))
        assert "serve" in names
        assert "backtest" in names
        assert "check" in names

    def test_backtest_subcommands(self):
        parser, _ = _build_parser()
        args = parser.parse_args(["backtest", "run", ".", "--strategy", "Demo"])
        assert args.command == "backtest"
        assert args.backtest_command == "run"
        assert args.strategy == "Demo"

    def test_check_factor_parses(self):
        parser, _ = _build_parser()
        args = parser.parse_args(["check", "factor", ".", "-f", "close"])
        assert args.command == "check"
        assert args.check_command == "factor"
        assert args.factor == "close"

    def test_serve_api_only_flag(self):
        parser, _ = _build_parser()
        args = parser.parse_args(["serve", "--api-only"])
        assert args.api_only is True


class TestDispatchRouting:
    def test_backtest_run_calls_cmd_run(self, monkeypatch):
        calls = []
        import strategy_research.interfaces.cli.commands.merged as m

        monkeypatch.setattr(
            m, "cmd_run", lambda args: calls.append("run") or 0,
            raising=False,
        )
        # dispatch imports from package; patch package attr
        import strategy_research.interfaces.cli as cli_pkg

        monkeypatch.setattr(cli_pkg, "cmd_run", lambda args: calls.append("run") or 0)
        ns = argparse.Namespace(backtest_command="run", path=".")
        assert dispatch_backtest(ns) == 0
        assert calls == ["run"]

    def test_backtest_rerun_calls_cmd_evaluate(self, monkeypatch):
        import strategy_research.interfaces.cli as cli_pkg

        calls = []
        monkeypatch.setattr(cli_pkg, "cmd_evaluate", lambda args: calls.append("ev") or 0)
        ns = argparse.Namespace(backtest_command="rerun", path=".")
        assert dispatch_backtest(ns) == 0
        assert calls == ["ev"]

    def test_backtest_replay_calls_cmd_reproduce(self, monkeypatch):
        import strategy_research.interfaces.cli as cli_pkg

        calls = []
        monkeypatch.setattr(cli_pkg, "cmd_reproduce", lambda args: calls.append("rp") or 0)
        ns = argparse.Namespace(backtest_command="replay", path=".", run="run_0001")
        assert dispatch_backtest(ns) == 0
        assert calls == ["rp"]

    def test_check_factor_calls_cmd_validate(self, monkeypatch):
        import strategy_research.interfaces.cli as cli_pkg

        calls = []
        monkeypatch.setattr(cli_pkg, "cmd_validate", lambda args: calls.append("v") or 0)
        ns = argparse.Namespace(check_command="factor", path=".", factor="close")
        assert dispatch_check(ns) == 0
        assert calls == ["v"]

    def test_check_unknown_returns_zero(self):
        ns = argparse.Namespace(check_command=None)
        assert dispatch_check(ns) == 0
        assert dispatch_backtest(argparse.Namespace(backtest_command=None)) == 0


class TestAddHelpers:
    def test_add_backtest_returns_parser(self):
        sp = argparse.ArgumentParser().add_subparsers()
        p = add_backtest_parser(sp)
        assert p is not None

    def test_add_check_returns_parser(self):
        sp = argparse.ArgumentParser().add_subparsers()
        p = add_check_parser(sp)
        assert p is not None
