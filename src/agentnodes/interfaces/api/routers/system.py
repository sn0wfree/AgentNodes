"""System info API — workspace status, LLM config, user count."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from agentnodes.foundation.config import paths as _paths
from agentnodes.foundation.config import settings as _settings
from agentnodes.foundation.utils import io_utils

logger = logging.getLogger(__name__)

router = APIRouter()

# M9-2（批 3c）：路径公式单源到 foundation.config.paths——此前 5 份游离
# 副本各自 Path.home()/.agentnodes 硬编码，profile 切换时失灵。旧常量名
# 保留作测试锚（tests patch 模块属性）；运行时求值访问器供新消费者直接用。
_QUANTNODES_DIR = _paths.data_dir()
_LLM_JSON_PATH = _paths.llm_json_path()
_DOTENV_PATH = _paths.dotenv_path()
_THEME_JSON_PATH = _QUANTNODES_DIR / "theme.json"


class LLMConfigUpdate(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None


# 配色统一阶段 3 §8 第 1 步：内置默认主题从前端 globals.css **自动提取**
# 全部颜色变量。theme.json 缺失/非法时回退到这里；改 CSS 变量后无需改后端代码。
# 判定从「值必须以颜色开头」改为「值中包含颜色字面量」：这样多值 box-shadow
# （`0 4px 14px rgba(...)`）与 linear-gradient 也能导出（§9 批 1）。
# 排除 space/radius/font/easing 等非颜色 token（它们不属配色控制范围）。
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_CSS_VAR_DECL = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")
_COLOR_IN_VALUE = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|color-mix\(|linear-gradient\(|radial-gradient\(|"
    r"\b(?:transparent|white|black|currentColor)\b"
)
_VAR_ONLY = re.compile(r"^var\(--[\w-]+\)$")
# §9 批 3：Tailwind RGB 通道变量（`14 165 233`）需可被 theme.json 覆盖，
# 否则 sky/blue/accent 色阶无法经配置换色。只接受 3 个 0-255 整数。
_RGB_CHANNELS = re.compile(r"^\d{1,3}\s+\d{1,3}\s+\d{1,3}$")

# §9 批 6：Tailwind 调色板「材料」变量（`--emerald-400-rgb`）。
# 这些是代码级常量（Tailwind 默认色板），**不导出到 theme.json**——
# 否则配置文件会退化成 1800 行色值清单，而不是"参数表"。
# 用户要改某族色相 → 改 roles 映射；要改某一档精确值 → 在 theme.dark
# 里显式覆盖该变量（仍被接受）。
_PALETTE_FAMILIES = (
    "emerald", "red", "rose", "violet", "purple", "amber", "orange", "yellow",
    "lime", "green", "teal", "cyan", "sky", "indigo", "fuchsia", "pink", "blue",
)
_MATERIAL_VAR = re.compile(r"^--(?:" + "|".join(_PALETTE_FAMILIES) + r")-\d+-rgb$")

# §9 批 6：语义角色 → 主色族（该角色在全站用量最大的那一族）。
# 配置文件写 roles.success="green" 时，主色族 emerald 的材质整体换成 green 的，
# 全站 emerald-* 用法随之变绿，组件零改动。
_ROLE_FAMILY: dict[str, str] = {
    "success": "emerald",
    "danger": "rose",
    "info": "sky",
    "thinking": "indigo",
    "warning": "orange",
}


def _is_material_var(name: str) -> bool:
    """色板材料变量（代码级常量）。"""
    return bool(_MATERIAL_VAR.match(name))


def _apply_role_families(values: dict[str, str], roles: dict[str, str]) -> None:
    """把「角色 → 色族」参数展开成具体材质值。

    roles.success="green" → 把 emerald 各档材质换成 green 同档的材质；
    其余色族保持自身材质（避免一个角色把同族其它色相也一起改掉）。
    """
    for role, family in roles.items():
        canonical = _ROLE_FAMILY.get(role)
        if not canonical or not isinstance(family, str) or family == canonical:
            continue
        if family not in _PALETTE_FAMILIES:
            logger.warning("theme roles: 未知色族 %r（角色 %s），已忽略", family, role)
            continue
        prefix, suffix = f"--{canonical}-", "-rgb"
        for name in list(values):
            if not (name.startswith(prefix) and name.endswith(suffix)):
                continue
            shade = name[len(prefix): -len(suffix)]
            src = f"--{family}-{shade}{suffix}"
            if src in values:
                values[name] = values[src]


def _is_color_value(value: str, name: str = "") -> bool:
    """值是颜色（或颜色别名变量）才纳入主题；多值 shadow/gradient 也算。"""
    stripped = value.strip()
    if _RGB_CHANNELS.match(stripped) and all(int(n) <= 255 for n in stripped.split()):
        return True
    return bool(_VAR_ONLY.match(stripped) or _COLOR_IN_VALUE.search(stripped) or name.endswith("-rgb"))


def _iter_theme_declarations(css: str):
    """逐块遍历 CSS 变量声明，产出 (mode, 变量名, 值)。

    globals.css 里 dark/light 块交替出现（:root / 主 light 块 / workflow
    canvas / DAG card），旧版「按首个 [data-theme='light'] 一刀切」会把
    后续 dark 块误归 light。这里用花括号状态机按每个块的选择器判 mode。
    """
    css = _CSS_COMMENT.sub("", css)
    depth = 0
    mode: str | None = None
    block: list[str] = []
    for line in css.splitlines():
        opens, closes = line.count("{"), line.count("}")
        if depth == 0 and opens:
            if "[data-theme='light']" in line or '[data-theme="light"]' in line:
                mode = "light"
            elif (
                ":root" in line
                or "[data-theme='dark']" in line
                or '[data-theme="dark"]' in line
            ):
                mode = "dark"
            else:
                mode = None
            block = []
        if mode:
            block.append(line)
        depth += opens - closes
        if depth <= 0:
            if mode:
                # 整个块一起匹配：多值 shadow / gradient 声明跨行，逐行会漏。
                for m in _CSS_VAR_DECL.finditer("\n".join(block)):
                    yield mode, m.group(1), m.group(2).strip()
            depth = 0
            mode = None
            block = []


def _extract_css_colors() -> dict[str, dict[str, str]]:
    """从 globals.css 提取 dark/light 双主题颜色变量（按块归属，非按首次切分）。"""
    # routers/system.py → parents[5] = 仓库根（src/agentnodes/interfaces/api/routers）
    css_path = Path(__file__).resolve().parents[5] / "webui" / "frontend" / "src" / "styles" / "globals.css"
    try:
        css = css_path.read_text(encoding="utf-8")
    except OSError:
        return {"dark": {}, "light": {}}
    result: dict[str, dict[str, str]] = {"dark": {}, "light": {}}
    for mode, name, value in _iter_theme_declarations(css):
        if _is_color_value(value, name):
            result[mode][name] = value
    return result


_BUILTIN_THEME: dict[str, dict[str, str]] = _extract_css_colors()


def _load_theme() -> dict[str, dict[str, str]]:
    """读取 theme.json；缺失/非法回退内置。

    §9 批 6 起支持两层结构（每主题）：

    ```json
    {"theme": {
       "dark": {
         "roles":    {"success": "green"},          // 参数：角色用哪个色相
         "overrides": {"--success-400-rgb": "52 211 153"}  // 精确覆盖
       },
       "light": {...}
    }}
    ```

    旧的扁平写法（直接 `"--var": "value"`）仍兼容。
    """
    result = {k: dict(v) for k, v in _BUILTIN_THEME.items()}
    try:
        raw = json.loads(_THEME_JSON_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return result
    candidate = raw.get("theme") if isinstance(raw, dict) else None
    if not isinstance(candidate, dict):
        return result
    for mode in ("dark", "light"):
        values = candidate.get(mode)
        if not isinstance(values, dict):
            continue
        # 第一层：角色 → 色相（参数，展开为具体材质）
        roles = values.get("roles")
        if isinstance(roles, dict):
            _apply_role_families(result[mode], roles)
        # 第二层：精确覆盖（含材料变量，用户想逐档微调时用）
        overrides = values.get("overrides")
        sources = [overrides] if isinstance(overrides, dict) else []
        sources.append(values)  # 兼容扁平写法
        for src in sources:
            for key, val in src.items():
                # 只接受字符串值，避免注入非法 CSS
                if isinstance(key, str) and key.startswith("--") and isinstance(val, str):
                    result[mode][key] = val
    return result


@router.get("/theme")
def get_theme():
    """配色配置（阶段 3）：前端启动时注入 CSS 变量，实现不改代码换肤。"""
    return _load_theme()


def _read_dotenv() -> dict[str, str]:
    """Read .env file into a dict.

    M9-3（批 3c）：解析单源 ``io_utils.read_dotenv``。与旧内联实现的
    语义差异（io_utils 契约）：值**原样保留**——不 strip 首尾空白、
    不剥引号（旧实现 ``v.strip()``）；文件不存在/不可读返回 ``{}``
    而非抛 OSError。
    """
    return io_utils.read_dotenv(_DOTENV_PATH)


def _write_dotenv(data: dict[str, str]) -> None:
    """Write dict to .env file, preserving other keys.

    M9-3 行为修复：原实现 ``Path.write_text`` 非原子（进程崩溃/并发
    写入可能留下半截 .env），改为 ``io_utils.atomic_write_text``
    （tmp + os.replace + 0600）。合并语义不变：``data`` 覆盖同名 key，
    其余 key 原样保留。
    """
    existing = _read_dotenv()
    existing.update(data)
    lines = [f"{k}={v}" for k, v in existing.items()]
    io_utils.atomic_write_text(_DOTENV_PATH, "\n".join(lines) + "\n")


@router.get("/info")
async def system_info():
    """Return system information for the settings modal."""
    from agentnodes.interfaces.api.user_db import get_user_db
    from agentnodes.interfaces.cli.llm_config_check import check_llm_config

    db = get_user_db()

    # Workspace path
    workspace = _settings.get_str("AGENTNODES_WORKSPACE") or str(Path.cwd())

    # LLM status (legacy dict for frontend compat)
    try:
        llm = check_llm_config()
    except (OSError, ValueError, RuntimeError):
        llm = {"configured": False, "provider": "unknown", "model": "unknown", "api_key_source": "unknown"}

    # Load full LLMConfig (carries user overrides) and resolve model info
    model_info = None
    try:
        from agentnodes.foundation.llm.config import LLMConfig
        from agentnodes.foundation.llm.model_catalog import get_model_info

        llm_config = LLMConfig.load()
        provider = llm_config.provider
        model = llm_config.model
        if provider and provider != "auto" and model:
            info = get_model_info(provider, model, user_config=llm_config)
            # Reflect user-config overrides in the legacy dict too so
            # the settings modal sees the same numbers.
            if llm_config.model_context_tokens is not None:
                llm["model_context_tokens"] = llm_config.model_context_tokens
            if llm_config.model_max_output_tokens is not None:
                llm["model_max_output_tokens"] = llm_config.model_max_output_tokens
            model_info = info.to_dict()
    except (OSError, ValueError, RuntimeError) as exc:
        logger.debug("Failed to load model info: %s", exc)

    # R-014：把重试上限作为统一配置暴露给前端（单一事实源 = llm.json 的
    # max_retries）。前端 AppShell 的会话初始化重试与此共用同一数值；
    # 服务不可达时前端走自己的默认兜底（见 AppShell FALLBACK_MAX_RETRIES）。
    max_retries = 3  # LLMConfig 默认（foundation/llm/config.py:165）
    try:
        from agentnodes.foundation.llm.config import LLMConfig
        max_retries = LLMConfig.load().max_retries
    except (OSError, ValueError, RuntimeError):
        pass
    llm["max_retries"] = max_retries

    return {
        "workspace_path": workspace,
        "user_count": db.user_count(),
        "llm": llm,
        "model_info": model_info,
    }


class ModelInfoRefreshRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None


@router.post("/model-info/refresh")
async def refresh_model_info(body: ModelInfoRefreshRequest):
    """Force-refresh model metadata from models.dev.

    Body is optional; if omitted, refreshes the currently configured
    provider/model. User-config overrides (model_context_tokens etc.)
    are applied on top of fetched values so the user's declared
    context window always wins.
    """
    from agentnodes.foundation.llm.config import LLMConfig
    from agentnodes.foundation.llm.model_catalog import refresh_model_info

    # Resolve provider/model — explicit body wins, else current LLMConfig
    provider = body.provider
    model = body.model
    user_config: LLMConfig | None = None
    try:
        user_config = LLMConfig.load()
        if not provider or not model:
            provider = provider or user_config.provider
            model = model or user_config.model
    except (OSError, ValueError, KeyError):
        pass

    if not provider or not model or provider == "auto" or model == "unknown":
        raise HTTPException(
            status_code=400,
            detail="provider and model are required (or configure LLM first)",
        )

    try:
        info = await refresh_model_info(
            provider, model, user_config=user_config
        )
        return info.to_dict()
    except (OSError, ValueError, RuntimeError) as exc:
        logger.exception("Model info refresh failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/llm")
async def get_llm_config():
    """Get the effective LLM configuration (profile-aware).

    Returns the resolved config (via LLMConfig, so an active provider
    profile is honoured), the active profile name, and a provider
    catalogue for the settings UI. API keys are always masked.
    """
    from agentnodes.foundation.llm.config import LLMConfig

    config = {
        "provider": "",
        "model": "",
        "base_url": "",
        "api_key": "",
        "api_key_masked": False,
        "key_var": "",
        "active_profile": _active_profile(_LLM_JSON_PATH) or "",
        "profiles": sorted(_load_profiles(_LLM_JSON_PATH)) or [],
        "providers": _provider_catalog(),
    }

    try:
        cfg = LLMConfig.load()
        provider = cfg.provider
        if provider == "auto":
            provider = ""
        config["provider"] = provider
        config["model"] = cfg.model if cfg.model != "unknown" else ""
        config["base_url"] = cfg.base_url or ""
        real_key = cfg.api_key or ""
        if real_key:
            config["api_key"] = _mask_key(real_key)
            config["api_key_masked"] = True
        if provider:
            config["key_var"] = _key_var_for(provider)
    except (OSError, ValueError, KeyError):
        logger.debug("Failed to resolve LLM config", exc_info=True)

    return config


@router.put("/llm")
async def update_llm_config(body: LLMConfigUpdate):
    """Update LLM configuration (profile-aware, atomic writes).

    - Sets ``active_profile`` and upserts the matching profile in
      ``llm.json["llm"]["profiles"]``.
    - Legacy configs (top-level provider/model, no profiles) are
      auto-migrated into the profile structure on first save.
    - A new API key is stored in ``~/.quantnodes/.env`` under the
      ``<PROVIDER>_API_KEY`` convention; masked values are never
      written back.
    """
    from agentnodes.interfaces.cli.commands.llm import (
        _atomic_write_llm_json,
        _backup_llm_json,
        _write_dotenv,
    )

    provider = body.provider or None
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")

    data = _load_llm_json(_LLM_JSON_PATH)
    llm = data.get("llm")
    if not isinstance(llm, dict):
        llm = {}
    llm = dict(llm)

    # ── Auto-migrate legacy top-level config into profiles ──────────
    profiles = llm.get("profiles")
    if not isinstance(profiles, dict):
        migrated: dict[str, dict] = {}
        legacy_provider = llm.get("provider")
        if legacy_provider:
            profile = {"provider": legacy_provider}
            for k in ("model", "base_url", "api_key"):
                if llm.get(k):
                    profile[k] = llm[k]
            migrated[legacy_provider] = profile
        profiles = migrated
    profiles = dict(profiles)

    # ── Upsert the active profile ───────────────────────────────────
    profile = dict(profiles.get(provider) or {})
    profile["provider"] = provider
    if body.model:
        profile["model"] = body.model
    if body.base_url:
        profile["base_url"] = body.base_url
    if body.api_key and "••••" not in body.api_key:
        env_var = _key_var_for(provider)
        profile["api_key"] = f"env:{env_var}"
        _write_dotenv({env_var: body.api_key}, dotenv_path=_DOTENV_PATH)

    profiles[provider] = profile
    llm["profiles"] = profiles
    llm["active_profile"] = provider
    llm.setdefault("timeout", 300)
    llm.setdefault("max_retries", 2)
    data["llm"] = llm

    _backup_llm_json(_LLM_JSON_PATH)
    _atomic_write_llm_json(data, llm_json_path=_LLM_JSON_PATH)

    return {
        "status": "ok",
        "provider": provider,
        "model": profile.get("model", ""),
        "active_profile": provider,
    }


# ── LLM helpers (shared with the `llm` CLI command) ─────────────────


def _load_llm_json(path: Path) -> dict:
    from agentnodes.interfaces.cli.commands.llm import (
        _load_llm_json as _cli_load_llm_json,
    )
    return _cli_load_llm_json(path)


def _load_profiles(path: Path) -> dict[str, dict]:
    from agentnodes.interfaces.cli.commands.llm import (
        _load_profiles as _cli_load_profiles,
    )
    return _cli_load_profiles(path)


def _active_profile(path: Path) -> str | None:
    from agentnodes.interfaces.cli.commands.llm import (
        _active_profile as _cli_active_profile,
    )
    return _cli_active_profile(path)


def _key_var_for(name: str) -> str:
    from agentnodes.interfaces.cli.commands.llm import _key_var_for as _cli_kv
    return _cli_kv(name)


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "••••"
    return key[:4] + "••••••••" + key[-4:]


def _provider_catalog() -> list[dict]:
    """name → {label, model, models, base_url, key_var, key_configured}.

    Union of the adapter registry and llm.json profiles; labels and
    suggested models come from the onboarding provider catalogue when
    available.
    """
    from agentnodes.foundation.llm.provider import (
        _REGISTRY,  # noqa: PLC2701
        get_provider_defaults,
    )

    profiles = _load_profiles(_LLM_JSON_PATH)
    dotenv = _read_dotenv()

    try:
        from agentnodes.interfaces.cli.onboard import PROVIDERS as ONBOARD
        labels = {p.key: (p.label, p.suggested_models)
                  for p in ONBOARD}
    except (ImportError, AttributeError, KeyError):
        labels = {}

    names = sorted(set(_REGISTRY) | set(profiles))
    catalog: list[dict] = []
    for name in names:
        if name in ("auto", "fallback"):
            continue
        label, suggested = labels.get(name, (name, ()))
        defaults = get_provider_defaults(name)
        profile = profiles.get(name) or {}
        key_var = _key_var_for(name)
        # 当前配置的 model 必须出现在候选列表首位（模型选择 fix
        # 2026-11-26）：此前 models 只含 onboarding 建议列表，用户配置的
        # model（如 MiniMax-M3.1-Flash）不在其中——下拉里看不到正在用的
        # 模型，误以为"模型选择被忽略"。
        models = list(suggested) if suggested else \
            ([defaults["model"]] if defaults.get("model") else [])
        current_model = profile.get("model") or defaults.get("model")
        if current_model and current_model not in models:
            models = [current_model, *models]
        catalog.append({
            "name": name,
            "label": label,
            "model": current_model or "",
            "models": models,
            "base_url": profile.get("base_url") or defaults.get("base_url") or "",
            "key_var": key_var,
            "key_configured": bool(
                os.environ.get(key_var) or dotenv.get(key_var)
            ),
        })
    return catalog
