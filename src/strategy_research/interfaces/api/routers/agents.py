"""Agent schemas API — structured JSON output schemas for each agent role.

Derived from the agent prompt files (``templates/.prompts/*.md``).
Schema is parsed once at startup and cached; automatically refreshed when
a prompt file's mtime changes (no server restart required).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import strategy_research
from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Schema cache ───────────────────────────────────────────────────────

_PROMPTS_DIR = (
    Path(strategy_research.__file__).parent
    / "templates" / ".prompts"
)

_schema_cache: dict[str, dict[str, Any]] = {}  # role → AgentSchema as dict
_schema_mtimes: dict[str, float] = {}          # role → file mtime

_CACHE_KEY = "schemas"  # top-level cache key


def _refresh_schema_cache() -> None:
    """Re-parse all agent schemas.  Called on startup and when mtime changes."""
    try:
        # routers/agents.py → three dots up to strategy_research.core
        from strategy_research.kernel.agent.prompt_schema_extractor import load_all_schemas
    except ImportError:
        logger.warning("prompt_schema_extractor not importable; skipping schema cache")
        return

    schemas = load_all_schemas(_PROMPTS_DIR)
    _schema_cache.clear()
    for role, schema in schemas.items():
        _schema_cache[role] = _schema_to_dict(schema)

    # Update mtimes
    _schema_mtimes.clear()
    for md_path in _PROMPTS_DIR.glob("*.md"):
        role = md_path.stem
        if not role.startswith("_"):
            _schema_mtimes[role] = md_path.stat().st_mtime

    logger.info("Agent schemas loaded: %s", list(_schema_cache.keys()))


def _schema_to_dict(schema: Any) -> dict[str, Any]:
    """Convert a dataclass AgentSchema to a plain dict for JSON response."""
    return {
        "role": schema.role,
        "fields": schema.fields,
        "field_hints": {
            k: {
                "label": v.label,
                "type": v.type,
                "core": v.core,
                "enum_values": v.enum_values,
                "format": v.format,
                "description": v.description,
            }
            for k, v in schema.field_hints.items()
        },
        "action_field": schema.action_field,
        "action_enum": schema.action_enum,
    }


def _check_and_refresh() -> None:
    """If any prompt file changed (mtime), refresh the cache."""
    for md_path in _PROMPTS_DIR.glob("*.md"):
        role = md_path.stem
        if role.startswith("_"):
            continue
        try:
            mtime = md_path.stat().st_mtime
        except OSError:
            continue
        if _schema_mtimes.get(role) != mtime:
            _refresh_schema_cache()
            return


# ── Startup hook ───────────────────────────────────────────────────────

def startup_warmup() -> None:
    """Pre-parse schemas at server startup (called from app startup event)."""
    _refresh_schema_cache()


# ── API endpoints ──────────────────────────────────────────────────────

@router.get("/schemas")
async def get_agent_schemas() -> dict[str, Any]:
    """Return JSON schemas for all agent roles.

    Response: ``{ "<role>": { role, fields, field_hints, action_field, ... } }``

    Cache is automatically refreshed when a prompt file's mtime changes.
    """
    _check_and_refresh()
    return dict(_schema_cache)
