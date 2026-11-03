"""Tests for ``cli.commands.slash_router``."""

from __future__ import annotations

import pytest

from strategy_research.interfaces.cli.commands.slash_router import (
    _ALIASES,
    SLASH_COMMANDS,
    Command,
    _parse_token,
    _score,
    find_exact,
    match_commands,
)

# ─── Command dataclass ─────────────────────────────────────────────────


class TestCommandDataclass:
    def test_frozen(self):
        cmd = Command("foo", "bar", "mod")
        with pytest.raises(Exception):
            cmd.name = "baz"  # type: ignore[misc]

    def test_fields(self):
        cmd = Command("name", "desc", "mod.path")
        assert cmd.name == "name"
        assert cmd.description == "desc"
        assert cmd.handler_module == "mod.path"


# ─── Registry invariants ────────────────────────────────────────────────


class TestRegistry:
    def test_builtin_commands_present(self):
        names = {cmd.name for cmd in SLASH_COMMANDS}
        for required in ("help", "model", "memory", "history", "goal", "search",
                         "swarm", "skill", "show", "clear", "pine", "journal",
                         "shadow", "export", "debug", "quit"):
            assert required in names

    def test_no_duplicate_names(self):
        names = [cmd.name for cmd in SLASH_COMMANDS]
        assert len(names) == len(set(names))

    def test_handler_module_is_string(self):
        for cmd in SLASH_COMMANDS:
            assert isinstance(cmd.handler_module, str)
            assert cmd.handler_module.startswith("cli.")

    def test_aliases_resolve_to_known(self):
        names = {cmd.name for cmd in SLASH_COMMANDS}
        for alias, target in _ALIASES.items():
            assert target in names, f"alias {alias} -> {target} not in registry"


# ─── _parse_token ───────────────────────────────────────────────────────


class TestParseToken:
    @pytest.mark.parametrize(
        "input_text,expected",
        [
            ("/me arg", "me"),
            ("/", ""),
            ("not a slash", ""),
            ("/foo bar baz", "foo"),
            ("  /spaced", "spaced"),
            ("/help", "help"),
            ("", ""),
        ],
    )
    def test_parse_token(self, input_text, expected):
        assert _parse_token(input_text) == expected


# ─── find_exact ─────────────────────────────────────────────────────────


class TestFindExact:
    def test_known(self):
        cmd = find_exact("help")
        assert cmd is not None
        assert cmd.name == "help"

    def test_alias_q(self):
        cmd = find_exact("q")
        assert cmd is not None
        assert cmd.name == "quit"

    def test_alias_exit(self):
        cmd = find_exact("exit")
        assert cmd is not None
        assert cmd.name == "quit"

    def test_alias_colon_q(self):
        cmd = find_exact(":q")
        assert cmd is not None
        assert cmd.name == "quit"

    def test_alias_question_mark(self):
        cmd = find_exact("?")
        assert cmd is not None
        assert cmd.name == "help"

    def test_unknown_returns_none(self):
        assert find_exact("nope") is None

    def test_empty_returns_none(self):
        assert find_exact("") is None


# ─── _score ─────────────────────────────────────────────────────────────


class TestScoring:
    def test_prefix_outranks_substring(self):
        assert _score("h", "help") > _score("h", "search")  # 'h' in 'search' via subseq

    def test_prefix_match(self):
        s = _score("hi", "history")
        assert s >= 100

    def test_substring_match(self):
        # 'mo' as substring in 'model' (o at index 1)
        s = _score("od", "model")
        assert 0 < s < 100  # substring tier

    def test_no_match_returns_zero_for_unknown_subseq(self):
        # 'xyz' not subsequence of 'help'
        assert _score("xyz", "help") == 0

    def test_zero_query_returns_zero(self):
        assert _score("", "help") == 0

    def test_subsequence_match(self):
        s = _score("hl", "help")
        assert s >= 10

    def test_empty_query_returns_zero(self):
        assert _score("", "help") == 0

    def test_no_match_returns_zero(self):
        assert _score("xyz", "help") == 0


# ─── match_commands ────────────────────────────────────────────────────


class TestMatchCommands:
    def test_prefix_match(self):
        results = match_commands("/h")
        names = {c.name for c in results}
        assert "history" in names  # 'h' is prefix
        assert "help" in names

    def test_substring_match(self):
        results = match_commands("/m")
        names = {c.name for c in results}
        # 'm' is a prefix match for many
        assert "model" in names
        assert "memory" in names

    def test_subsequence_match(self):
        results = match_commands("/hl")
        names = {c.name for c in results}
        assert "help" in names  # 'hl' subseq in 'help'

    def test_no_match(self):
        results = match_commands("/xyz_no_command")
        assert results == []

    def test_no_slash(self):
        results = match_commands("not a slash")
        assert results == []

    def test_limit_respected(self):
        results = match_commands("/s", limit=2)
        assert len(results) <= 2

    def test_order_includes_registry_order(self):
        # For prefix matches, longer names outrank shorter (90+len logic),
        # so 'shadow' (6 chars) outranks 'show' (4 chars). Test that.
        results = match_commands("/s")
        names = [c.name for c in results]
        # Longer prefix matches should be first
        shadow_idx = names.index("shadow") if "shadow" in names else -1
        show_idx = names.index("show") if "show" in names else -1
        if shadow_idx >= 0 and show_idx >= 0:
            assert shadow_idx < show_idx  # shadow beats show

    def test_case_insensitive(self):
        results = match_commands("/HELP")
        names = {c.name for c in results}
        assert "help" in names
