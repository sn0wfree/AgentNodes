"""Web search with provider auto-detection.

Backend priority (first match wins):
  1. MiniMax Token Plan / Coding Plan — if a key is configured in the
     environment (``MINIMAX_CODE_PLAN_KEY`` etc.) the search goes through
     ``api.minimaxi.com/v1/coding_plan/search`` (CN by default;
     ``MINIMAX_API_HOST`` overrides the host).
  2. DuckDuckGo — fallback when no MiniMax key is configured. No key
     required, but the package ``duckduckgo-search`` must be installed.

Both backends return the same JSON shape so downstream callers
(WebSearchTool, agent loop, frontend SearchPanel) can use either
without branching.
"""

from __future__ import annotations

import json
import logging

from ._rate_limit import ExponentialBackoff

logger = logging.getLogger(__name__)

# 模块级单例限速器
_backoff = ExponentialBackoff(base=1.0, max_delay=30.0, factor=2.0)


def web_search(
    query: str,
    max_results: int = 10,
) -> str:
    """Web search via the best available backend.

    Auto-detects: prefers MiniMax when an env key is set, otherwise
    falls back to DuckDuckGo.

    Args:
        query: Search query string.
        max_results: Maximum number of results (default 10). For
            MiniMax this is clamped to the API's 1..10 range; for
            DuckDuckGo it is the literal cap.

    Returns:
        JSON string with search results. ``provider`` is set to
        ``"minimax"`` or ``"duckduckgo"`` in the payload for downstream
        observability.
    """
    if not query or not query.strip():
        return json.dumps({
            "status": "error",
            "error": "query is required",
        }, ensure_ascii=False)

    # Try MiniMax first (preferred: official API, structured payload).
    try:
        from .minimax_search import has_minimax_credentials, minimax_search
        if has_minimax_credentials():
            payload = minimax_search(query, count=max_results)
            # Chat API key ≠ Coding Plan search key — 400/invalid params
            # must fall through to DuckDuckGo instead of failing the tool.
            try:
                parsed = json.loads(payload) if isinstance(payload, str) else payload
            except Exception:  # noqa: BLE001
                parsed = None
            if isinstance(parsed, dict) and parsed.get("status") == "error":
                logger.warning(
                    "minimax_search error for %r: %s; falling back to DuckDuckGo",
                    query, parsed.get("error"),
                )
            else:
                return payload
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "minimax_search dispatch failed for %r: %s; "
            "falling back to DuckDuckGo",
            query, exc,
        )

    return _duckduckgo_search(query, max_results)


def _duckduckgo_search(query: str, max_results: int) -> str:
    """DuckDuckGo fallback (kept separate for testability)."""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return json.dumps({
            "status": "error",
            "error": "duckduckgo-search package not installed. Install with: pip install duckduckgo-search",
        }, ensure_ascii=False)

    try:
        _backoff.wait()
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        _backoff.reset()

        return json.dumps({
            "status": "ok",
            "provider": "duckduckgo",
            "query": query,
            "max_results": max_results,
            "n_results": len(results),
            "results": [
                {
                    "title": r.get("title", ""),
                    "href": r.get("href", ""),
                    "body": r.get("body", ""),
                }
                for r in results
            ],
        }, ensure_ascii=False)

    except Exception as exc:
        logger.warning("web_search failed for %r: %s", query, exc)
        return json.dumps({
            "status": "error",
            "error": f"search failed: {exc}",
            "query": query,
        }, ensure_ascii=False)
