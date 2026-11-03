"""Builtin workflow template loading.

Definitions live in ``templates/workflows/*.json`` (shipped with the
package, read-only) and ``<workspace>/workflows/*.json`` (user,
writable).  User definitions shadow builtins with the same name.

Design: docs/workflow-module-design.md §9
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .definition import WorkflowDefinition, WorkflowDefinitionError

logger = logging.getLogger(__name__)

_BUILTIN_DIR = Path(__file__).resolve().parent.parent.parent / "templates" / "workflows"


def builtin_dir() -> Path:
    return _BUILTIN_DIR


def user_dir(workspace: Path) -> Path:
    return Path(workspace) / "workflows"


def list_builtin_names() -> list[str]:
    """Builtin definition names (sorted)."""
    if not _BUILTIN_DIR.is_dir():
        return []
    return sorted(p.stem for p in _BUILTIN_DIR.glob("*.json"))


def load_definition(
    name: str,
    workspace: Path,
    *,
    prefer: str = "user",
) -> WorkflowDefinition | None:
    """Load a definition: user first (default), then builtin.

    Returns None when not found; raises WorkflowDefinitionError on
    invalid content.
    """
    orders = ("user", "builtin") if prefer == "user" else ("builtin", "user")
    for source in orders:
        base = user_dir(workspace) if source == "user" else _BUILTIN_DIR
        path = base / f"{name}.json"
        if path.is_file():
            return WorkflowDefinition.load(path, source=source)
    return None


def list_definitions(workspace: Path) -> list[dict[str, Any]]:
    """All definitions with source markers (user shadows builtin)."""
    user = _load_dir(user_dir(workspace), "user")
    builtin = _load_dir(_BUILTIN_DIR, "builtin")
    merged = {d["name"]: d for d in user}
    for d in builtin:
        if d["name"] not in merged:
            merged[d["name"]] = d
    return [merged[k] for k in sorted(merged)]


def _load_dir(base: Path, source: str) -> list[dict[str, Any]]:
    if not base.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(base.glob("*.json")):
        try:
            definition = WorkflowDefinition.load(path, source=source)
            result.append({"name": definition.name, "source": source,
                           "description": definition.description,
                           "node_count": len(definition.nodes)})
        except WorkflowDefinitionError as exc:
            logger.warning("skip invalid workflow %s: %s", path, exc)
    return result


def save_user_definition(definition: WorkflowDefinition, workspace: Path) -> Path:
    """Persist a definition into the user workflows dir."""
    path = user_dir(workspace) / f"{definition.name}.json"
    definition.save(path)
    return path


def delete_user_definition(name: str, workspace: Path) -> bool:
    path = user_dir(workspace) / f"{name}.json"
    if path.is_file():
        path.unlink()
        return True
    return False


__all__ = [
    "builtin_dir", "user_dir", "list_builtin_names", "load_definition",
    "list_definitions", "save_user_definition", "delete_user_definition",
]
