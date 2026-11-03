"""MiniMax web search via the Token Plan / Coding Plan search API.

Spec: https://docs2.openclaw.ai/tools/minimax-search

Auth (priority order):
    MINIMAX_CODE_PLAN_KEY  >  MINIMAX_CODING_API_KEY  >
    MINIMAX_OAUTH_TOKEN    >  MINIMAX_API_KEY

Region (default: CN ``api.minimaxi.com``):
    - ``MINIMAX_API_HOST`` env override
    - fallback to CN if neither is set

The output schema mirrors ``web_search`` so existing Agent code that
calls ``web_search`` transparently switches to MiniMax when a key is
configured, with no agent-side changes.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


# ── Auth resolution (priority order, matches OpenClaw spec) ──
ENV_KEYS: tuple[str, ...] = (
    "MINIMAX_CODE_PLAN_KEY",
    "MINIMAX_CODING_API_KEY",
    "MINIMAX_OAUTH_TOKEN",
    "MINIMAX_API_KEY",
)

# ── Endpoints (default: CN) ──
CN_BASE_URL = "https://api.minimaxi.com/v1/coding_plan/search"
GLOBAL_BASE_URL = "https://api.minimax.io/v1/coding_plan/search"
DEFAULT_BASE_URL = CN_BASE_URL

_TIMEOUT_SECONDS = 15.0


def _resolve_api_key() -> str | None:
    """Return the first non-empty MiniMax key from env, or None."""
    for name in ENV_KEYS:
        v = os.environ.get(name)
        if v:
            return v
    return None


def _resolve_base_url() -> str:
    """Pick the search endpoint based on ``MINIMAX_API_HOST`` env.

    Resolution rules:
      1. ``MINIMAX_API_HOST`` env containing ``minimaxi.com`` → CN
      2. ``MINIMAX_API_HOST`` env containing ``minimax.io`` → global
      3. default → CN
    """
    host = (os.environ.get("MINIMAX_API_HOST") or "").lower()
    if "minimaxi.com" in host:
        return CN_BASE_URL
    if "minimax.io" in host:
        return GLOBAL_BASE_URL
    return DEFAULT_BASE_URL


def has_minimax_credentials() -> bool:
    """True if any MiniMax Token Plan key is configured in the env."""
    return _resolve_api_key() is not None


def minimax_search(query: str, count: int = 5) -> str:
    """Run a MiniMax Token Plan web search and return a JSON string.

    The output schema is identical to ``web_search``:
    ``{status, query, count, n_results, results: [{title, href, body}, ...],
       related_queries: [...]}`` so callers can swap providers without
    parsing differences.

    Args:
        query: search query string (required, non-empty).
        count: 1-10 inclusive, default 5 (MiniMax clamps if out of range).
    """
    if not query or not query.strip():
        return json.dumps({
            "status": "error",
            "error": "query is required",
        }, ensure_ascii=False)

    api_key = _resolve_api_key()
    if not api_key:
        return json.dumps({
            "status": "error",
            "error": (
                "no MiniMax API key configured "
                "(set MINIMAX_CODE_PLAN_KEY or MINIMAX_API_KEY)"
            ),
            "query": query,
        }, ensure_ascii=False)

    count = max(1, min(10, int(count) if count else 5))
    url = _resolve_base_url()
    body = json.dumps({"query": query, "count": count}).encode("utf-8")

    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        # Surface status code + brief body excerpt
        body_excerpt = ""
        try:
            body_excerpt = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        logger.warning(
            "minimax_search HTTP %d for %r: %s",
            exc.code, query, body_excerpt or exc.reason,
        )
        return json.dumps({
            "status": "error",
            "error": f"HTTP {exc.code}: {body_excerpt or exc.reason}",
            "query": query,
        }, ensure_ascii=False)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("minimax_search failed for %r: %s", query, exc)
        return json.dumps({
            "status": "error",
            "error": f"search failed: {exc}",
            "query": query,
        }, ensure_ascii=False)

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        logger.warning("minimax_search: non-JSON response: %r", payload[:200])
        return json.dumps({
            "status": "error",
            "error": f"non-JSON response: {exc}",
            "query": query,
        }, ensure_ascii=False)

    # MiniMax response shape (tolerant of variants):
    #   {"results": [{title, url, snippet, ...}], "related_queries": [...]}
    #   or {"organic": [...]}  (older format)
    raw_results = (
        data.get("results")
        or data.get("organic")
        or data.get("items")
        or []
    )
    parsed = []
    for r in raw_results:
        if not isinstance(r, dict):
            continue
        parsed.append({
            "title": r.get("title", "") or r.get("name", ""),
            "href": (
                r.get("url")
                or r.get("link")
                or r.get("href")
                or ""
            ),
            "body": (
                r.get("snippet")
                or r.get("description")
                or r.get("summary")
                or r.get("content")
                or ""
            ),
        })

    return json.dumps({
        "status": "ok",
        "provider": "minimax",
        "query": query,
        "count": count,
        "n_results": len(parsed),
        "results": parsed,
        "related_queries": data.get("related_queries", [])
        or data.get("related", []),
    }, ensure_ascii=False)


__all__ = [
    "minimax_search",
    "has_minimax_credentials",
    "_resolve_api_key",
    "_resolve_base_url",
    "ENV_KEYS",
]
