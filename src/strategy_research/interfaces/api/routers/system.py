"""System info API — workspace status, LLM config, user count."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

_QUANTNODES_DIR = Path.home() / ".quantnodes"
_LLM_JSON_PATH = _QUANTNODES_DIR / "llm.json"
_DOTENV_PATH = _QUANTNODES_DIR / ".env"


class LLMConfigUpdate(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None


def _read_dotenv() -> dict[str, str]:
    """Read .env file into a dict."""
    result = {}
    if _DOTENV_PATH.exists():
        for line in _DOTENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip()
    return result


def _write_dotenv(data: dict[str, str]) -> None:
    """Write dict to .env file, preserving other keys."""
    existing = _read_dotenv()
    existing.update(data)
    lines = [f"{k}={v}" for k, v in existing.items()]
    _DOTENV_PATH.write_text("\n".join(lines) + "\n")
    _DOTENV_PATH.chmod(0o600)


@router.get("/info")
async def system_info():
    """Return system information for the settings modal."""
    from strategy_research.interfaces.api.user_db import get_user_db
    from strategy_research.interfaces.cli.llm_config_check import check_llm_config

    db = get_user_db()

    # Workspace path
    workspace = os.environ.get("SR_WORKSPACE_PATH", str(Path.cwd()))

    # LLM status (legacy dict for frontend compat)
    try:
        llm = check_llm_config()
    except (OSError, ValueError, RuntimeError):
        llm = {"configured": False, "provider": "unknown", "model": "unknown", "api_key_source": "unknown"}

    # Load full LLMConfig (carries user overrides) and resolve model info
    model_info = None
    try:
        from strategy_research.foundation.llm.config import LLMConfig
        from strategy_research.foundation.llm.model_catalog import get_model_info

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
    from strategy_research.foundation.llm.config import LLMConfig
    from strategy_research.foundation.llm.model_catalog import refresh_model_info

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
    from strategy_research.foundation.llm.config import LLMConfig

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
    from strategy_research.interfaces.cli.commands.llm import (
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
    from strategy_research.interfaces.cli.commands.llm import (
        _load_llm_json as _cli_load_llm_json,
    )
    return _cli_load_llm_json(path)


def _load_profiles(path: Path) -> dict[str, dict]:
    from strategy_research.interfaces.cli.commands.llm import (
        _load_profiles as _cli_load_profiles,
    )
    return _cli_load_profiles(path)


def _active_profile(path: Path) -> str | None:
    from strategy_research.interfaces.cli.commands.llm import (
        _active_profile as _cli_active_profile,
    )
    return _cli_active_profile(path)


def _key_var_for(name: str) -> str:
    from strategy_research.interfaces.cli.commands.llm import _key_var_for as _cli_kv
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
    from strategy_research.foundation.llm.provider import (
        _REGISTRY,  # noqa: PLC2701
        get_provider_defaults,
    )

    profiles = _load_profiles(_LLM_JSON_PATH)
    dotenv = _read_dotenv()

    try:
        from strategy_research.interfaces.cli.onboard import PROVIDERS as ONBOARD
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
        catalog.append({
            "name": name,
            "label": label,
            "model": profile.get("model") or defaults.get("model") or "",
            "models": list(suggested) if suggested else
                      ([defaults["model"]] if defaults.get("model") else []),
            "base_url": profile.get("base_url") or defaults.get("base_url") or "",
            "key_var": key_var,
            "key_configured": bool(
                os.environ.get(key_var) or dotenv.get(key_var)
            ),
        })
    return catalog
