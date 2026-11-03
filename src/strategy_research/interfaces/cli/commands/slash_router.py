"""Slash command registry + fuzzy matcher.

Mirrors ``vibe-trading/cli/commands/slash_router.py``. Provides:

* :class:`Command` — frozen dataclass for a slash-command entry.
* :data:`SLASH_COMMANDS` — tuple of all built-in commands.
* :func:`match_commands` — tiered fuzzy match (prefix/substring/subsequence).
* :func:`find_exact` — exact lookup with alias resolution.
* :func:`_parse_token` — extract the command token from ``/foo arg``.

Order in :data:`SLASH_COMMANDS` is the display order in typeahead.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    """A single slash command entry."""

    name: str
    description: str
    handler_module: str


# Order here = order shown in bare ``/`` typeahead. Group by frequency.
SLASH_COMMANDS: tuple[Command, ...] = (
    Command("help", "Show keyboard shortcuts and command list", "cli.commands.help"),
    Command("model", "Switch LLM provider and model", "cli.commands.chat"),
    Command("memory", "Show / manage persistent memory", "cli.commands.memory"),
    Command("history", "Browse and resume prior sessions", "cli.commands.session"),
    Command("goal", "Start / inspect a finance research goal", "cli.commands.goal"),
    Command("search", "Full-text search across all sessions", "cli.commands.session"),
    Command("swarm", "Multi-agent presets (committee / quant / risk)", "cli.commands.chat"),
    Command("skill", "List / load / unload skills", "cli.commands.show"),
    Command("show", "Show prior run by id", "cli.commands.show"),
    Command("clear", "Clear current conversation", "cli.commands.chat"),
    Command("pine", "Export current strategy as Pine Script", "cli.commands.show"),
    Command("journal", "Analyze trade journal CSV", "cli.commands.chat"),
    Command("shadow", "Train / view shadow account", "cli.commands.chat"),
    Command("export", "Export current session (md / json)", "cli.commands.session"),
    Command("debug", "Toggle debug panel (token usage / latency)", "cli.commands.chat"),
    Command("halt", "Kill switch — halt long-running loops now", "cli.commands.slash_halt"),
    Command("resume", "Clear the kill switch", "cli.commands.slash_halt"),
    Command("quit", "Exit (also: q, exit, :q)", "cli.commands.chat"),
)


# Aliases — same handler, different surface keyword. Kept separate from
# the main registry so typeahead does not duplicate rows.
_ALIASES: dict[str, str] = {
    "q": "quit",
    "exit": "quit",
    ":q": "quit",
    "?": "help",
}


def _parse_token(input_text: str) -> str:
    """Strip the leading ``/`` and isolate the command token.

    >>> _parse_token("/me arg")
    'me'
    >>> _parse_token("/")
    ''
    >>> _parse_token("not a slash")
    ''
    """
    text = input_text.lstrip()
    if not text.startswith("/"):
        return ""
    parts = text[1:].split(None, 1)
    return parts[0] if parts else ""


def find_exact(name: str) -> Command | None:
    """Resolve alias then exact-match the registry."""
    target = _ALIASES.get(name, name)
    for cmd in SLASH_COMMANDS:
        if cmd.name == target:
            return cmd
    return None


def _score(query: str, candidate: str) -> int:
    """Tiered scoring for fuzzy matching.

    Higher score wins. Ties broken by registry order. Returns ``0`` when
    nothing matches.
    """
    if not query:
        return 0

    candidate_lower = candidate.lower()
    query_lower = query.lower()

    # Tier 1: prefix match (highest).
    if candidate_lower.startswith(query_lower):
        return 100 + len(candidate_lower)

    # Tier 2: substring match.
    idx = candidate_lower.find(query_lower)
    if idx >= 0:
        return 50 + len(candidate_lower) - idx

    # Tier 3: subsequence match.
    qi = 0
    matched = 0
    for ch in candidate_lower:
        if qi < len(query_lower) and ch == query_lower[qi]:
            qi += 1
            matched += 1
    if qi == len(query_lower):
        return 10 + matched

    return 0


def match_commands(input_text: str, *, limit: int = 8) -> list[Command]:
    """Return commands matching the (possibly partial) ``/foo`` input.

    Scoring tiers (highest wins): prefix > substring > subsequence.
    Returns an empty list if nothing matches.

    A bare ``/`` (no query yet) returns all commands up to ``limit``
    so the typeahead can show the full menu immediately after ``/``.
    """
    query = _parse_token(input_text)
    text = input_text.lstrip()
    if not text.startswith("/"):
        return []
    if not query:
        return list(SLASH_COMMANDS[:limit])
    scored: list[tuple[int, int, Command]] = []
    for idx, cmd in enumerate(SLASH_COMMANDS):
        s = _score(query, cmd.name)
        if s > 0:
            scored.append((-s, idx, cmd))
    scored.sort()
    return [cmd for _, _, cmd in scored[:limit]]


__all__ = ["Command", "SLASH_COMMANDS", "find_exact", "match_commands"]
