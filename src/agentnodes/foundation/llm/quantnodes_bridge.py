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
#   - Env-override keys retained as "AGENTNODES__LLM__*" per project choice
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

def _profile_home_dir(profile: str) -> str:
    """取 profile 的 home 目录名（``.agentnodes`` / ``.quantnodes``）。

    不硬编码品牌字面量——路径字面量门禁（``check_path_literals``）
    会拦旧品牌硬编码，且 profile 改名时这里自动跟上。
    """
    from agentnodes.foundation.config.paths import PROFILES

    return PROFILES[profile].home_dir_name


#: **默认写入目标**（写入方 `cli/commands/llm.py` / `api/routers/system.py`
#: 都写这里）。批 D 之前它同时是"唯一读取位置"——那是 bug（见 CONFIG_PATHS）。
CONFIG_PATH: Final[Path] = (
    Path.home() / _profile_home_dir("agentnodes") / "llm.json"
)

#: **读取路径链**（按序，第一个存在且解析成功的赢）。
#:
#: 批 D（2026-11-11）修：此前这里是 `(CONFIG_PATH,)` 单元素——2026-11-05
#: 的"数据目录统一"（bd3f6009）把 CONFIG_PATH 从 quantnodes 目录改到
#: agentnodes 目录，旧路径就此退出读取链，但十几处 docstring 仍宣称它是
#: "兼容回退"。**quantnodes 用自己那份 llm.json 配置，那份配置实际完全
#: 读不到。**
#:
#: 顺序理由：
#: 1. `config.yaml` —— agentnodes 新格式；同目录下两个都有 = 用户正在迁移，
#:    新格式更可能是"他想要的最新意图"；
#: 2. `llm.json`（agentnodes 目录）—— 老位置，写入方在这里；
#: 3. `llm.json`（quantnodes 目录）—— **quantnodes 契约**；属于另一个
#:    消费方，本进程只做兼容读取，不该压过 agentnodes 自己的配置。
def _llm_config_paths() -> tuple[Path, ...]:
    """构造读取链（**运行时求值**，不固化 ``Path.home()``）。"""
    an_dir = Path.home() / _profile_home_dir("agentnodes")
    qn_dir = Path.home() / _profile_home_dir("quantnodes")
    return (
        an_dir / "config.yaml",
        an_dir / "llm.json",
        qn_dir / "llm.json",
    )


CONFIG_PATHS: Final[tuple[Path, ...]] = _llm_config_paths()

#: 支持的配置载体后缀。`.yaml`/`.yml` 走 YAML 解析，其余走 JSON。
_YAML_SUFFIXES: Final[frozenset[str]] = frozenset({".yaml", ".yml"})

_ENV_OVERRIDE_KEYS: Final[tuple[str, ...]] = (
    "AGENTNODES__LLM__PROVIDER",
    "AGENTNODES__LLM__MODEL",
    "AGENTNODES__LLM__BASE_URL",
    "AGENTNODES__LLM__API_KEY",
    "AGENTNODES__LLM__TIMEOUT",
    "AGENTNODES__LLM__MAX_RETRIES",
    "AGENTNODES__LLM__MAX_TOKENS",
    "AGENTNODES__LLM__ENABLED",
)


# ---------------------------------------------------------------------------
# ``env:VAR`` expander (verbatim from
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


def _load_any_path(path: Path) -> dict[str, Any] | None:
    """按后缀分派：``.yaml``/``.yml`` 走 YAML，其余走 JSON。

    批 D 新增。返回值语义与 :func:`_load_single_path` **完全一致**：

    - 文件不存在 → ``None``（调用方继续试下一个候选）；
    - 存在但解析失败 → ``{}`` + warn。**关键：不返回 None**——否则
      用户的坏 config.yaml 会被"跳过"，看起来像"没配置"，正是批 E
      治的那类静默失效；
    - 存在但无 ``llm`` 段 → ``{}``（同为"找到了但为空"）。

    YAML 解析器缺失时（极端环境）：**warn 并返回 None**（当作该路径
    不存在，让调用方继续试下一个候选）——因为"不支持这个格式"与
    "这个文件坏了"是两回事，前者不该毒化整条链。
    """
    if not path.exists():
        return None

    if path.suffix.lower() in _YAML_SUFFIXES:
        try:
            import yaml
        except ImportError:                      # pragma: no cover - 环境异常
            logger.warning(
                "%s: YAML config found but PyYAML is not installed — "
                "skipping this path (install with: pip install pyyaml)",
                path,
            )
            return None
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:      # noqa: BLE001 - YAMLError/OSError/UnicodeError
            # yaml.YAMLError 不在 Exception 之外；OSError（读失败）与
            # UnicodeDecodeError 也在族内。统一降级为"找到了但为空"。
            logger.warning("Failed to parse %s: %s", path, exc)
            return {}
        if data is None:
            # 空 YAML 文件 = 显式空配置（与 JSON 的 `{}` 同义）。
            return {}
        if not isinstance(data, dict):
            logger.warning(
                "%s: top-level is not a mapping (%s)", path, type(data).__name__,
            )
            return {}
        llm_section = data.get("llm")
        if not isinstance(llm_section, dict):
            return {}
        return llm_section

    return _load_single_path(path)


# ---------------------------------------------------------------------------
# Env-override application (verbatim from
# QuantNodes/research/common/llm/client.py)
# ---------------------------------------------------------------------------


def _apply_env_overrides(llm_cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow-copied llm_cfg with AGENTNODES__LLM__* env vars applied.

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
      2. ``CONFIG_PATHS`` 路径链（批 D 起三元素，见常量注释）：
         ``~/.agentnodes/config.yaml`` → ``~/.agentnodes/llm.json``
         → ``~/.quantnodes/llm.json``。第一个**存在**的赢（判据见
         :func:`_load_any_path`：坏文件算"找到了但为空"，不跳过）。
      3. Returns ``{}`` if not found。

    If ``resolve_api_key`` is True, the returned dict's ``api_key`` field has
    any ``env:VAR`` syntax expanded against ``os.environ`` before return.
    """
    if config_path is not None:
        result = _load_any_path(config_path)
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
            result = _load_any_path(path)
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
