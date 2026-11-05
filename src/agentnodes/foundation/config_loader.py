"""Generic layered config loader — 4-layer merge utility.

Many subsystems in this codebase resolve their configuration from the same
priority stack:

    1. CLI overrides (truthy-only, immediate wins)
    2. Workspace-level YAML  (e.g. ``<workspace>/acceptance.yaml``)
    3. User-level YAML       (e.g. ``~/.quantnodes-research/acceptance.yaml``)
    4. Built-in defaults     (dataclass defaults or constructor defaults)

Before Phase 2.1, each subsystem reimplemented the same merge loop
(``core/llm/config.py:LLMConfig.load``,
``core/strategy_acceptance/__init__.py:load_config``,
``core/goal/workflow_config.py``). This module provides the shared
implementation plus a Builder-pattern facade for expressive call sites.

Public API
----------
    load_layered_config(...)
        The flat function used by the simple cases.
    ConfigBuilder(...)
        Builder-pattern wrapper used by more complex configs (e.g. LLMConfig
        which also applies provider defaults, env-var translation, and
        separate ``api_key`` resolution).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ── Layered merge (the simple flat function) ─────────────────────────


def load_layered_config(
    *,
    defaults: Mapping[str, Any],
    cli_overrides: Mapping[str, Any] | None = None,
    workspace_path: Path | None = None,
    user_path: Path | None = None,
    path_resolver: Callable[[Path], Path] | None = None,
    key_filter: Callable[[str, Any], bool] | None = None,
) -> dict[str, Any]:
    """Merge four config sources into one dict.

    Priority (high → low): cli_overrides > workspace_path > user_path > defaults.

    Args:
        defaults:        Built-in defaults (typically ``dataclass.asdict(...)``).
        cli_overrides:   CLI-arg dict; only truthy keys are applied.
        workspace_path:  Optional path to a project-level YAML. ``None`` skips.
        user_path:       Optional path to a user-level YAML. ``None`` skips.
        path_resolver:   Optional callable that maps ``Path`` → resolved path
                         (e.g. to apply ``Path.home().expanduser()``). Receives
                         the input path verbatim; may return a different path.
        key_filter:      Optional ``(key, value) -> bool`` predicate. Returning
                         ``False`` excludes the key from the merged result.
                         Use this to enforce type validation or restrict to
                         a known key set (forward-compat ignore unknown).

    Returns:
        A new dict with the merged result.

    Notes:
        * Missing YAML files are silent (returns empty dict).
        * Malformed YAML raises ``yaml.YAMLError`` (transparent from PyYAML).
        * Unknown keys in any layer are passed through unless ``key_filter``
          rejects them.
        * ``None`` values in CLI overrides are dropped (lets users pass
          argparse-style args where unset args are ``None``).
    """
    layers: list[dict[str, Any]] = []

    # 3) User-level YAML (lower priority than workspace)
    if user_path is not None:
        user_resolved = path_resolver(user_path) if path_resolver else user_path
        user_dict = _read_yaml(user_resolved)
        if user_dict:
            layers.append(user_dict)

    # 2) Workspace-level YAML
    if workspace_path is not None:
        ws_resolved = path_resolver(workspace_path) if path_resolver else workspace_path
        ws_dict = _read_yaml(ws_resolved)
        if ws_dict:
            layers.append(ws_dict)

    # 1) CLI overrides (highest priority)
    if cli_overrides:
        cli_dict = {k: v for k, v in dict(cli_overrides).items() if v is not None}
        if cli_dict:
            layers.append(cli_dict)

    merged: dict[str, Any] = dict(defaults)
    for layer in layers:
        for key, value in layer.items():
            if value is None:
                continue
            if key_filter is not None and not key_filter(key, value):
                continue
            merged[key] = value

    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read YAML file; return empty dict if missing or non-mapping root."""
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (FileNotFoundError, yaml.YAMLError) as exc:
        logger.debug("Could not read YAML %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


# ── Common key filters ───────────────────────────────────────────────


def scalar_key_filter(key: str, value: Any) -> bool:
    """Accept only scalar (str / int / float / bool) values.

    Matches the ``strategy_acceptance.load_config`` semantics where YAML
    lists / mappings are silently dropped. Use this when the config
    schema is flat scalars.
    """
    return isinstance(value, (str, int, float, bool))


def known_keys_filter(allowed: set[str]) -> Callable[[str, Any], bool]:
    """Filter to a closed set of keys; rejects anything else.

    Use this to enforce a strict schema (vs. the default which passes
    unknown keys through for forward-compat).
    """
    def _filter(key: str, value: Any) -> bool:
        return key in allowed
    return _filter


# ── Builder (for complex configs that need additional transformations)


class ConfigBuilder:
    """Fluent builder for assembling configs from heterogeneous sources.

    Useful when the 4-layer merge is not enough — e.g. LLMConfig also
    applies provider defaults, env-var translation, and a separate
    ``api_key`` resolution chain.

    The builder composes the same merge as ``load_layered_config`` but
    lets callers inject pre/post hooks per layer.

    Example::

        cfg = (ConfigBuilder(defaults={...})
               .with_user_yaml(Path("~/.config/foo.yaml"))
               .with_workspace_yaml(Path("./foo.yaml"))
               .with_cli_overrides(args)
               .with_post_hook(_apply_provider_defaults)
               .build())
    """

    def __init__(self, defaults: Mapping[str, Any]) -> None:
        self._defaults: dict[str, Any] = dict(defaults)
        self._layers: list[tuple[str, dict[str, Any]]] = []
        self._post_hooks: list[Callable[[dict[str, Any]], dict[str, Any] | None]] = []

    def with_user_yaml(self, path: Path | None) -> "ConfigBuilder":
        if path is not None:
            self._layers.append(("user", _read_yaml(path)))
        return self

    def with_workspace_yaml(self, path: Path | None) -> "ConfigBuilder":
        if path is not None:
            self._layers.append(("workspace", _read_yaml(path)))
        return self

    def with_yaml_data(self, label: str, data: Mapping[str, Any] | None) -> "ConfigBuilder":
        """Inject a pre-parsed YAML dict (for callers that already read it)."""
        if data:
            self._layers.append((label, dict(data)))
        return self

    def with_cli_overrides(self, overrides: Mapping[str, Any] | None) -> "ConfigBuilder":
        if overrides:
            cli_dict = {k: v for k, v in dict(overrides).items() if v is not None}
            if cli_dict:
                self._layers.append(("cli", cli_dict))
        return self

    def with_post_hook(
        self,
        hook: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> "ConfigBuilder":
        """Register a transformation applied after the merge.

        The hook receives the merged dict and may return a replacement dict
        (or ``None`` to leave it unchanged).
        """
        self._post_hooks.append(hook)
        return self

    def build(self) -> dict[str, Any]:
        """Apply all layers and hooks in registration order.

        Priority order is reverse of registration: later ``with_*`` calls
        override earlier ones (mirrors the precedence in
        ``load_layered_config``).
        """
        merged: dict[str, Any] = dict(self._defaults)
        for _label, layer in self._layers:
            for key, value in layer.items():
                if value is None:
                    continue
                merged[key] = value
        for hook in self._post_hooks:
            result = hook(merged)
            if result is not None:
                merged = result
        return merged


__all__ = [
    "ConfigBuilder",
    "known_keys_filter",
    "load_layered_config",
    "scalar_key_filter",
]
