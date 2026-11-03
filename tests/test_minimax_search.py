"""Tests for MiniMax web search backend."""
from __future__ import annotations

import json
import os
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from strategy_research.foundation.web.minimax_search import (
    ENV_KEYS,
    _resolve_api_key,
    _resolve_base_url,
    has_minimax_credentials,
    minimax_search,
)


class TestResolveApiKey:
    """Env-key resolution logic."""

    def test_returns_none_when_no_env_set(self, monkeypatch):
        for k in ENV_KEYS:
            monkeypatch.delenv(k, raising=False)
        assert _resolve_api_key() is None

    def test_returns_first_set_key(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_CODE_PLAN_KEY", "key-cp")
        monkeypatch.setenv("MINIMAX_API_KEY", "key-api")
        assert _resolve_api_key() == "key-cp"

    def test_falls_through_to_oauth(self, monkeypatch):
        for k in ENV_KEYS:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("MINIMAX_OAUTH_TOKEN", "oauth-123")
        assert _resolve_api_key() == "oauth-123"

    def test_has_credentials_matches_key(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_CODE_PLAN_KEY", raising=False)
        assert has_minimax_credentials() is False
        monkeypatch.setenv("MINIMAX_CODE_PLAN_KEY", "test")
        assert has_minimax_credentials() is True


class TestResolveBaseUrl:
    """Region selection logic."""

    def test_default_is_cn(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_API_HOST", raising=False)
        assert "minimaxi.com" in _resolve_base_url()

    def test_cn_when_host_contains_minimaxi_com(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_HOST", "https://api.minimaxi.com/v1")
        assert "minimaxi.com" in _resolve_base_url()

    def test_global_when_host_contains_minimax_io(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_HOST", "https://api.minimax.io/v1")
        assert "minimax.io" in _resolve_base_url()


class TestMinimaxSearch:
    """minimax_search() output schema + error handling."""

    def test_empty_query_returns_error(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_CODE_PLAN_KEY", "test")
        result = json.loads(minimax_search(""))
        assert result["status"] == "error"
        assert "query is required" in result["error"]

    def test_whitespace_only_query_returns_error(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_CODE_PLAN_KEY", "test")
        result = json.loads(minimax_search("   "))
        assert result["status"] == "error"

    def test_no_key_returns_error(self, monkeypatch):
        for k in ENV_KEYS:
            monkeypatch.delenv(k, raising=False)
        result = json.loads(minimax_search("quantum computing"))
        assert result["status"] == "error"
        assert "no MiniMax" in result["error"]

    def test_success_returns_correct_shape(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_CODE_PLAN_KEY", "test-key")
        mock_data = {
            "results": [
                {"title": "Momentum Strategy", "url": "https://example.com", "snippet": "A good strategy"},
            ],
            "related_queries": ["value investing"],
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = json.loads(minimax_search("momentum", count=5))

        assert result["status"] == "ok"
        assert result["provider"] == "minimax"
        assert result["n_results"] == 1
        assert result["results"][0]["title"] == "Momentum Strategy"
        assert result["results"][0]["href"] == "https://example.com"
        assert result["related_queries"] == ["value investing"]

    def test_count_clamped_to_1_10(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_CODE_PLAN_KEY", "test-key")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"results": []}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock:
            # count=100 should be clamped to 10
            json.loads(minimax_search("test", count=100))
            # urllib.request.Request(url, data=..., method=...)
            # The data= arg is the second positional arg
            req = mock.call_args[0][0]
            call_body = json.loads(req.data.decode())
            assert call_body["count"] == 10

    def test_http_error_returns_error(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_CODE_PLAN_KEY", "test-key")
        exc = urllib.error.HTTPError(
            url="https://api.minimaxi.com/v1/coding_plan/search",
            code=401, msg="Unauthorized", hdrs={}, fp=MagicMock(),
        )
        exc.read = MagicMock(return_value=b'{"error":"auth failed"}')

        with patch("urllib.request.urlopen", side_effect=exc):
            result = json.loads(minimax_search("test"))
            assert result["status"] == "error"
            assert "401" in result["error"]

    def test_uses_organic_fallback_key(self, monkeypatch):
        """When 'results' is absent but 'organic' is present, use 'organic'."""
        monkeypatch.setenv("MINIMAX_CODE_PLAN_KEY", "test-key")
        mock_data = {
            "organic": [
                {"title": "From organic", "link": "https://example.org", "description": "organic desc"},
            ],
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = json.loads(minimax_search("test", count=1))
        assert result["results"][0]["title"] == "From organic"
        assert result["results"][0]["href"] == "https://example.org"
        assert result["results"][0]["body"] == "organic desc"


class TestMinimaxSearchEdgeCases:
    """Additional edge cases for minimax_search()."""

    def test_timeout_returns_error(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_CODE_PLAN_KEY", "test-key")
        with patch("urllib.request.urlopen", side_effect=TimeoutError("slow")):
            result = json.loads(minimax_search("test"))
            assert result["status"] == "error"
            assert "failed" in result["error"].lower()

    def test_non_json_response_returns_error(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_CODE_PLAN_KEY", "test-key")
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<html>403 Forbidden</html>"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = json.loads(minimax_search("test"))
            assert result["status"] == "error"
            assert "non-JSON" in result["error"]

    def test_empty_results_list(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_CODE_PLAN_KEY", "test-key")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"results": []}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = json.loads(minimax_search("zzz non-existent"))
            assert result["status"] == "ok"
            assert result["n_results"] == 0

    def test_non_dict_result_in_list_skipped(self, monkeypatch):
        """Non-dict items in the results array are gracefully ignored."""
        monkeypatch.setenv("MINIMAX_CODE_PLAN_KEY", "test-key")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "results": ["bad-string", 42, {"title": "good", "url": "https://ok.com", "snippet": "ok"}],
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = json.loads(minimax_search("test"))
            assert result["n_results"] == 1
            assert result["results"][0]["href"] == "https://ok.com"


class TestWebSearchFallback:
    """web_search() auto-selects provider."""

    def test_falls_to_ddg_when_no_key(self, monkeypatch):
        for k in ENV_KEYS:
            monkeypatch.delenv(k, raising=False)

        # Should not raise — falls through to DDG (which itself may
        # fail if ddg isn't installed, but we just want to verify the
        # fallback path is entered).
        from strategy_research.foundation.web.search import web_search
        result = json.loads(web_search("quantum computing"))
        # result is either "ok" (DDG works) or "error" (DDG not installed)
        assert result["status"] in ("ok", "error")

    def test_minimax_preferred_when_key_set(self, monkeypatch):
        """When MINIMAX_CODE_PLAN_KEY is set, web_search uses minimax_search."""
        monkeypatch.setenv("MINIMAX_CODE_PLAN_KEY", "test-key")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "results": [{"title": "Momentum", "url": "https://example.com", "snippet": "A strategy"}],
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            from strategy_research.foundation.web.search import web_search
            result = json.loads(web_search("momentum"))
            assert result["status"] == "ok"
            assert result["provider"] == "minimax"
