# SPDX-License-Identifier: MIT
#
# Derived from QuantNodes (https://github.com/sn0wfree/QuantNodes).
# Original sources:
#   - QuantNodes/research/common/llm/client.py (CONFIG_PATH, CONFIG_PATHS,
#     _load_single_path, _apply_env_overrides, _ENV_OVERRIDE_KEYS,
#     load_llm_config).
#   - QuantNodes/research/common/llm/resolver.py (_expand_env_var).
#
# Modifications:
#   - Renamed entry point to load_quantnodes_llm_config(); kept
#     load_llm_config as an alias for back-compat.
#   - _expand_env_var() is invoked on the "api_key" field inside the loader
#     when resolve_api_key=True, so "env:VAR" syntax is resolved before
#     the dict is returned to callers.
#   - Env-override keys retained as "QUANTNODES__LLM__*" per project choice
#     (α1: keep namespace compatible with QuantNodes).
#
# 本文件直接复用 QuantNodes 的 reader 与 env:VAR 展开器，按 MIT 协议 relicense。
# 见 https://github.com/sn0wfree/QuantNodes/blob/main/pyproject.toml
# 原始版权属于原作者。

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_PATH: Final[Path] = Path.home() / ".quantnodes" / "llm.json"
CONFIG_PATHS: Final[tuple[Path, ...]] = (CONFIG_PATH,)

_ENV_OVERRIDE_KEYS: Final[tuple[str, ...]] = (
    "QUANTNODES__LLM__PROVIDER",
    "QUANTNODES__LLM__MODEL",
    "QUANTNODES__LLM__BASE_URL",
    "QUANTNODES__LLM__API_KEY",
    "QUANTNODES__LLM__TIMEOUT",
    "QUANTNODES__LLM__MAX_RETRIES",
    "QUANTNODES__LLM__MAX_TOKENS",
    "QUANTNODES__LLM__ENABLED",
)


# ---------------------------------------------------------------------------
# env:VAR expander (verbatim from
# QuantNodes/research/common/llm/resolver.py)
# ---------------------------------------------------------------------------


def _expand_env_var(value: str) -> str:
    """Expand ``env:VAR_NAME`` syntax in api_key field.

    Returns empty string when the referenced env var is unset.
    """
    if isinstance(value, str) and value.startswith("env:"):
        var_name = value[4:]
        return os.environ.get(var_name, "")
    return value


# ---------------------------------------------------------------------------
# Single-path loader (verbatim from
# QuantNodes/research/common/llm/client.py)
# ---------------------------------------------------------------------------


def _load_single_path(path: Path) -> dict[str, Any] | None:
    """Load the ``[llm]`` section from a single config file.

    Returns:
        - ``None`` if the file does not exist (so caller can try the next
          path in ``CONFIG_PATHS``).
        - ``{}`` if the file exists but has no ``"llm"`` top-level key
          (deliberate empty config — caller treats this as "found").
        - The ``llm`` dict otherwise.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to parse %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "%s: top-level is not a dict (%s)", path, type(data).__name__,
        )
        return {}
    llm_section = data.get("llm")
    if not isinstance(llm_section, dict):
        return {}
    return llm_section


# ---------------------------------------------------------------------------
# Env-override application (verbatim from
# QuantNodes/research/common/llm/client.py)
# ---------------------------------------------------------------------------


def _apply_env_overrides(llm_cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow-copied llm_cfg with QUANTNODES__LLM__* env vars applied.

    Order of preference: explicit env var beats file value.
    Empty / unset env vars do not overwrite.
    Bool coercion for ENABLED.
    """
    result = dict(llm_cfg)
    for key in _ENV_OVERRIDE_KEYS:
        env_val = os.environ.get(key)
        if env_val is None or env_val == "":
            continue
        field_name = key.rsplit("__", 1)[-1].lower()
        if field_name == "enabled":
            result[field_name] = env_val.lower() in ("1", "true", "yes", "on")
        else:
            result[field_name] = env_val
    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def load_quantnodes_llm_config(
    config_path: Path | None = None,
    *,
    resolve_api_key: bool = True,
) -> dict[str, Any]:
    """Load LLM config from the canonical QuantNodes path.

    Resolution order (first match wins):
      1. ``config_path`` argument (if provided) — overrides everything;
         used by tests and tooling.
      2. ``CONFIG_PATH`` = ``~/.quantnodes/llm.json`` (single canonical).
      3. Returns ``{}`` if not found.

    If ``resolve_api_key`` is True, the returned dict's ``api_key`` field has
    any ``env:VAR`` syntax expanded against ``os.environ`` before return.
    """
    if config_path is not None:
        result = _load_single_path(config_path)
        if result is None:
            logger.warning(
                "LLM config not found at %s", config_path,
            )
            return {}
        logger.debug(
            "[quantnodes_bridge] config loaded from explicit %s", config_path,
        )
        merged = _apply_env_overrides(result)
    else:
        merged = None
        for path in CONFIG_PATHS:
            result = _load_single_path(path)
            if result is not None:
                logger.info(
                    "[quantnodes_bridge] config loaded from %s", path,
                )
                merged = _apply_env_overrides(result)
                break
        if merged is None:
            logger.warning(
                "LLM config not found in any of: %s",
                [str(p) for p in CONFIG_PATHS],
            )
            return {}

    if resolve_api_key and isinstance(merged.get("api_key"), str):
        merged["api_key"] = _expand_env_var(merged["api_key"])

    return merged


# Back-compat alias matching the symbol used inside QuantNodes itself.
load_llm_config = load_quantnodes_llm_config


__all__ = [
    "CONFIG_PATH",
    "CONFIG_PATHS",
    "load_quantnodes_llm_config",
    "load_llm_config",
]
