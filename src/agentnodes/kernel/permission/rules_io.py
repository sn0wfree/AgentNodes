"""Permission ruleset persistence.

Rules live at ``~/.quantnodes-research/permissions.yaml``. The file
is auto-created on first launch with the default ruleset. Users can
edit it by hand — YAML parse failures degrade to empty ruleset
(safe default = ask).

Append-only API:
    * ``save_rule(rule)`` adds a new rule at the end (so it wins
      over earlier entries by last-match-wins).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from .evaluator import DEFAULT_RULES
from .schema import PermissionAction, PermissionRule

logger = logging.getLogger(__name__)

DEFAULT_RULES_PATH = Path(
    os.environ.get(
        "STRATEGY_RESEARCH_PERMISSIONS_PATH",
        str(Path.home() / ".quantnodes-research" / "permissions.yaml"),
    )
)


_HEADER_COMMENT = """# Permission ruleset (auto-managed).
#
# Resolution order is *last-match-wins*. To grant a tool an exception,
# add a more specific rule below the broad default. Format:
#
#   - permission: write_file | run_command | run_backtest | ... | "*"
#     pattern:    <glob>          # path for file tools, first command
#                                # token for run_command, strategy name
#                                # for run_backtest, "*" otherwise
#     action:     allow | ask | deny
#     comment:    optional
#
# Use the in-app 'Always allow' / 'Always reject' button on a
# permission prompt to append a rule here automatically.
"""


def _coerce_rule(d: Any) -> PermissionRule:
    """Tolerate both dict and dataclass-shaped YAML rows."""
    if isinstance(d, PermissionRule):
        return d
    if not isinstance(d, dict):
        raise ValueError(f"Invalid rule entry: {d!r}")
    action_raw = d.get("action", "ask")
    try:
        action = PermissionAction(str(action_raw))
    except ValueError:
        logger.warning(
            "permission rule has unknown action=%s — coercing to ask",
            action_raw,
        )
        action = PermissionAction.ASK
    return PermissionRule(
        permission=str(d.get("permission", "*")),
        pattern=str(d.get("pattern", "*")),
        action=action,
        comment=str(d.get("comment", "")),
    )


def load_rules(path: Path = DEFAULT_RULES_PATH) -> list[PermissionRule]:
    """Read the YAML file. Missing or malformed → defaults.

    Returns a NEW list; mutating it does not affect any cached
    ``PermissionEvaluator`` — callers should pass the result to
    ``evaluator.reload()``.
    """
    if not path.exists():
        return list(DEFAULT_RULES)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("permission rules unreadable (%s) — using defaults", exc)
        return list(DEFAULT_RULES)

    try:
        raw = yaml.safe_load(text) or []
    except yaml.YAMLError as exc:
        logger.warning(
            "permission rules malformed (%s) — falling back to defaults", exc,
        )
        return list(DEFAULT_RULES)

    if not isinstance(raw, list):
        logger.warning(
            "permission rules root must be a list — got %s", type(raw).__name__,
        )
        return list(DEFAULT_RULES)

    out: list[PermissionRule] = []
    for entry in raw:
        try:
            out.append(_coerce_rule(entry))
        except Exception as exc:  # noqa: BLE001
            logger.warning("skipping invalid permission rule %r: %s", entry, exc)
    return out


def save_rule(
    rule: PermissionRule,
    path: Path = DEFAULT_RULES_PATH,
) -> None:
    """Append a single rule to the YAML file (last-match-wins priority).

    No-op if a structurally identical rule is already the last entry
    (avoids the user spamming the same "always" pick). Writes are
    atomic via ``tempfile + os.replace`` so a crashed write cannot
    leave a truncated file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = load_rules(path)
    if existing and _rules_equal(existing[-1], rule):
        return  # no change

    existing.append(rule)
    _write_yaml(path, existing)


def write_defaults(path: Path = DEFAULT_RULES_PATH) -> None:
    """Force-overwrite the rules file with the defaults. Used on
    first launch and from the ``reset permissions`` admin action."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(path, list(DEFAULT_RULES))


def _rules_equal(a: PermissionRule, b: PermissionRule) -> bool:
    return (
        a.permission == b.permission
        and a.pattern == b.pattern
        and a.action == b.action
    )


def _write_yaml(path: Path, rules: list[PermissionRule]) -> None:
    payload: list[dict[str, Any]] = [r.to_dict() for r in rules]
    body = yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_HEADER_COMMENT + body, encoding="utf-8")
    os.replace(tmp, path)
