"""从 toml 装载 ChannelSpec（批次 14 · V5 第 2 步）。

## 职责边界

- ``channel.py``：**行为实现**（怎么提取思考、怎么映射错误）
- 本文件：**配置装载**（从哪个 toml 读、如何合并 ``extends``）

## 解析顺序（首次命中即用）

1. 用户覆盖：``<AN_MODEL_CATALOG_DIR>/model_specs/<provider>/<model>.toml``
   （允许用户在宿主目录覆盖内置行为，无需改包）
2. 内置：``foundation/llm/data/providers/<provider_id>/<model>.toml``
3. 缺省：``ChannelSpec()``（空配置 = 旧行为，安全降级）

## ``extends = "<channel>"`` 语义

模型 toml 顶层可写 ``extends = "minimax"``，表示"先取渠道默认，再用
本文件的 ``[channel]`` 覆盖"。渠道默认来自同目录的 ``_channel.toml``。

**刻意做成显式**：不做隐式合并——不写 ``extends`` 就是完全自足，
调试时不会出现"我不知道它还继承了别处什么"的情况。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agentnodes.foundation import settings as _settings

from .channel import ChannelSpec, ErrorRule

logger = logging.getLogger(__name__)

__all__ = ["load_channel_spec", "merge_channel_dicts"]

try:  # Python 3.11+
    import tomllib  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore[import-untyped,no-redef]

from . import MODELS_DEV_ID as _MODELS_DEV_ID  # noqa: E402  (循环安全：仅取映射)

#: 内置模型/渠道规格目录（``foundation/llm/data/providers``）。
#: 注意：本模块在 ``provider/`` 下，数据在 ``llm/data/``——是上上级。
_BUNDLED_DIR = Path(__file__).resolve().parent.parent / "data" / "providers"

#: 渠道默认文件名（``extends`` 指向它）。
_CHANNEL_DEFAULT_NAME = "_channel.toml"


def _user_specs_dir() -> Path:
    """用户级 provider 配置目录（可选覆盖）。

    走 ``settings`` 读取层（旧名 ``AN_MODEL_CATALOG_DIR`` 由 alias 索引
    兼容），不裸读 env——见 tests/test_settings.py 的
    ``test_no_undeclared_bare_env_reads`` 守卫。
    """
    import os

    from agentnodes.foundation import paths as _paths

    override = _settings.get_str("AGENTNODES_MODEL_CATALOG_DIR").strip()
    if override:
        base = Path(override).expanduser()
    else:
        home = Path(os.environ.get("HOME") or str(Path.home()))
        base = home / _paths.active_profile().home_dir_name
    return base / "model_specs"


def _read_toml(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 —— 坏配置不阻断对话
        logger.warning("channel spec: failed to parse %s: %s", path, exc)
        return None


def _find_model_toml(provider: str, model: str) -> dict[str, Any] | None:
    """按 用户覆盖 → 内置 的顺序找一个模型的 toml。

    **文件名匹配**：模型名可能含 ``/``（如 ``z-ai/glm-5.2``），文件名
    里用 ``_`` 代替（``z-ai_glm-5.2.toml``）——models.dev 的风格。
    """
    dev_id = _MODELS_DEV_ID.get(provider, provider)
    safe_model = model.replace("/", "_")
    candidates = (
        (_user_specs_dir() / provider / f"{model}.toml"),
        (_user_specs_dir() / provider / f"{safe_model}.toml"),
        (_user_specs_dir() / dev_id / f"{model}.toml"),
        (_user_specs_dir() / dev_id / f"{safe_model}.toml"),
        (_BUNDLED_DIR / dev_id / f"{model}.toml"),
        (_BUNDLED_DIR / dev_id / f"{safe_model}.toml"),
        (_BUNDLED_DIR / provider / f"{model}.toml"),
        (_BUNDLED_DIR / provider / f"{safe_model}.toml"),
    )
    for cand in candidates:
        data = _read_toml(cand)
        if data is not None:
            return data
    # 大小写不敏感回退（models.dev 文件名风格不一：MiniMax-M3 vs gpt-4o-mini）
    targets = {f"{model}.toml".lower(), f"{safe_model}.toml".lower()}
    for base in (_BUNDLED_DIR / dev_id, _BUNDLED_DIR / provider):
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if entry.name.lower() in targets:
                return _read_toml(entry)
    return None


def _load_channel_default(provider: str) -> dict[str, Any]:
    """读渠道默认 ``_channel.toml``（缺失返回空 dict）。"""
    dev_id = _MODELS_DEV_ID.get(provider, provider)
    for base in (_user_specs_dir() / provider, _BUNDLED_DIR / dev_id,
                 _BUNDLED_DIR / provider):
        data = _read_toml(base / _CHANNEL_DEFAULT_NAME)
        if data is not None:
            channel = data.get("channel")
            return channel if isinstance(channel, dict) else {}
    return {}


def merge_channel_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """合并两层 ``[channel]`` 段（override 优先）。

    规则：
    - **标量/列表**：override 有则整体替换（列表不拼接——顺序即语义，
      拼接会让 ``thinking_fields`` 的优先级混乱）
    - **字典**（headers / payload / stream_options）：浅合并
    - **error_rules**：override **非空则整体替换**（不做追加；追加会让
      "我的规则排第几"变得不可预测）
    """
    if not override:
        return dict(base)
    if not base:
        return dict(override)

    out = dict(base)
    for key, value in override.items():
        if key in ("headers", "payload", "stream_options") \
                and isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return out


def load_channel_spec(provider: str, model: str | None) -> ChannelSpec:
    """装载一个模型的渠道行为配置（永不抛异常，最差返回空 spec）。

    :param provider: 内部 provider 名（``minimax`` / ``deepseek`` …）
    :param model: 模型名（``MiniMax-M3.1-Flash``）；None/空 → 空 spec。
    """
    if not model:
        return ChannelSpec()
    try:
        data = _find_model_toml(provider, model)
        if data is None:
            return ChannelSpec()

        channel = data.get("channel")
        channel = channel if isinstance(channel, dict) else {}

        extends = data.get("extends")
        if isinstance(extends, str) and extends.strip():
            base = _load_channel_default(provider)
            channel = merge_channel_dicts(base, channel)

        return ChannelSpec.from_dict(channel)
    except Exception as exc:  # noqa: BLE001 —— 配置问题绝不阻断对话
        logger.warning(
            "channel spec: load failed for %s/%s: %s", provider, model, exc
        )
        return ChannelSpec()


def load_error_rules(provider: str, model: str | None) -> tuple[ErrorRule, ...]:
    """便捷入口：只取错误规则（供 adapter 组装 ``handle_error``）。"""
    return load_channel_spec(provider, model).error_rules


def load_model_limit(provider: str, model: str | None, key: str) -> int | None:
    """读模型 toml 的 ``[limit].<key>``（context / output）。

    批次 14 · V5 第 4 步：adapter 的 ``default_max_tokens`` /
    ``default_context_tokens`` 改为从这里读——**toml 是元数据唯一事实源**，
    消除"adapter 硬编码 / toml / 用户 llm.json"三处不一致。

    :param key: ``"context"`` 或 ``"output"``。
    :return: 数值；文件缺失或键不存在时返回 None（调用方回退默认值）。
    """
    if not model:
        return None
    try:
        data = _find_model_toml(provider, model)
        if data is None:
            return None
        limit = data.get("limit")
        if not isinstance(limit, dict):
            return None
        value = limit.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(value)
    except Exception:  # noqa: BLE001 —— 配置问题回落默认
        return None
