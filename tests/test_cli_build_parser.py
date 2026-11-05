"""Regression: CLI subcommands must register exactly once.

Phase 2.2 migration left both ``_build_parser()`` and
``@cli_command``-driven ``_wire()`` adding the same top-level
subparsers, which made every ``quantnodes-research`` invocation die
with ``argparse.ArgumentError: conflicting subparser``.
"""
from __future__ import annotations

import argparse

from strategy_research.interfaces.cli import _build_parser
from strategy_research.interfaces.cli.commands.registry import registered_commands

# Commands that live in the registry (core_commands.py + llm.py).
# autoresearch 已随 apps/autoresearch 移除（2026-11-05 批次 3）。
REGISTRY_COMMANDS = frozenset({
    "evaluate", "import", "init", "list",
    "llm", "preflight", "reproduce", "run", "status", "validate",
})


def _subcommand_names(parser: argparse.ArgumentParser) -> list[str]:
    for action in parser._actions:
        if action.dest == "command" and hasattr(action, "choices"):
            return sorted(action.choices)
    return []


class TestBuildParserSingleRegistration:
    def test_build_does_not_raise(self):
        parser, _ = _build_parser()
        assert isinstance(parser, argparse.ArgumentParser)

    def test_all_registered_commands_appear(self):
        parser, _ = _build_parser()
        names = set(_subcommand_names(parser))
        for name in registered_commands():
            assert name in names

    def test_legacy_commands_still_present(self):
        parser, _ = _build_parser()
        names = set(_subcommand_names(parser))
        # export / goal / hypothesis / validate-run / portfolio 已随对应 apps 包
        # 移除（2026-11-04 做减法批次 2）。
        for name in ("session", "skills", "swarm", "mcp", "api", "webui", "serve"):
            assert name in names

    def test_parse_known_core_command(self):
        parser, _ = _build_parser()
        args = parser.parse_args(["status", "."])
        assert args.command == "status"
        assert getattr(args, "handler", None) is not None

    def test_parse_llm_command(self):
        parser, _ = _build_parser()
        args = parser.parse_args(["llm", "--list"])
        assert args.command == "llm"
        assert getattr(args, "handler", None) is not None

    def test_serve_port_is_valid(self):
        parser, _ = _build_parser()
        args = parser.parse_args(["serve"])
        assert 0 < args.port < 65536
