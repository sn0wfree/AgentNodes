"""Tests for Web I/O: web_search, read_url."""

from __future__ import annotations

import json

import pytest


def _network_available() -> bool:
    """Check if the project's own web-search stack can reach a backend.

    **2026-11-10 修正**：旧实现只探测 DuckDuckGo（``duckduckgo_search``
    包），而 DDG 的 bing 中转在部分地区返回 None → 判据恒 False → 11 条
    真实搜索测试全部 skip。但项目自己的 ``web_search`` 是**多后端**的
    （MiniMax 优先、DDG 兜底），用 DDG 代表全部后端是错的判据。

    改为询问 ``web_search`` 本体：它能返回非 error 结果就算网络可用。
    """
    try:
        import json as _json

        from agentnodes.foundation.web.search import web_search

        out = _json.loads(web_search("test", max_results=1))
        return out.get("status") != "error"
    except Exception:
        return False


HAS_NETWORK = _network_available()


# ============================================================
# web_search
# ============================================================


class TestWebSearch:
    @pytest.mark.skipif(not HAS_NETWORK, reason="network unavailable")
    def test_search_happy_path(self):
        from agentnodes.foundation.web.search import web_search
        result = web_search("python programming", max_results=3)
        body = json.loads(result)
        assert body["status"] == "ok"
        assert body["query"] == "python programming"
        assert body["n_results"] >= 0

    def test_search_empty_query(self):
        from agentnodes.foundation.web.search import web_search
        result = web_search("")
        body = json.loads(result)
        assert body["status"] == "error"
        assert "required" in body["error"].lower()

    @pytest.mark.skipif(not HAS_NETWORK, reason="network unavailable")
    def test_search_max_results(self):
        from agentnodes.foundation.web.search import web_search
        result = web_search("test query", max_results=2)
        body = json.loads(result)
        assert body["status"] == "ok"
        assert body["max_results"] == 2
        if body["n_results"] > 0:
            assert len(body["results"]) <= 2

    @pytest.mark.skipif(not HAS_NETWORK, reason="network unavailable")
    def test_search_result_format(self):
        from agentnodes.foundation.web.search import web_search
        result = web_search("linux kernel", max_results=1)
        body = json.loads(result)
        assert body["status"] == "ok"
        if body["n_results"] > 0:
            r = body["results"][0]
            assert "title" in r
            assert "href" in r
            assert "body" in r

    def test_search_rate_limit_exists(self):
        from agentnodes.foundation.web._rate_limit import ExponentialBackoff
        b = ExponentialBackoff(base=0.01, max_delay=0.1)
        b.wait()
        assert b.current_delay >= 0.01
        b.reset()
        assert b.current_delay == 0.01

    def test_search_exponential_backoff(self):
        from agentnodes.foundation.web._rate_limit import ExponentialBackoff
        b = ExponentialBackoff(base=0.01, max_delay=0.5, factor=2.0)
        initial = b.current_delay
        b.wait()
        assert b.current_delay > initial
        b.wait()
        assert b.current_delay > initial * 2

    def test_search_backoff_reset(self):
        from agentnodes.foundation.web._rate_limit import ExponentialBackoff
        b = ExponentialBackoff(base=0.01, max_delay=0.5)
        b.wait()
        b.wait()
        b.reset()
        assert b.current_delay == 0.01

    def test_search_format(self):
        from agentnodes.foundation.web.search import web_search
        # 空查询测试格式
        result = web_search("")
        body = json.loads(result)
        assert "status" in body


# ============================================================
# read_url
# ============================================================


class TestReadUrl:
    @pytest.mark.skipif(not HAS_NETWORK, reason="network unavailable")
    def test_fetch_happy_path(self):
        from agentnodes.foundation.web.fetch import read_url
        result = read_url("https://httpbin.org/html", max_chars=5000)
        body = json.loads(result)
        assert body["status"] == "ok"
        assert "markdown" in body
        assert body["char_count"] > 0

    def test_fetch_empty_url(self):
        from agentnodes.foundation.web.fetch import read_url
        result = read_url("")
        body = json.loads(result)
        assert body["status"] == "error"
        assert "required" in body["error"].lower()

    @pytest.mark.skipif(not HAS_NETWORK, reason="network unavailable")
    def test_fetch_truncation(self):
        from agentnodes.foundation.web.fetch import read_url
        result = read_url("https://httpbin.org/html", max_chars=100)
        body = json.loads(result)
        assert body["status"] == "ok"
        if body["char_count"] > 100:
            assert body["truncated"] is True
            assert "[truncated]" in body["markdown"]

    @pytest.mark.skipif(not HAS_NETWORK, reason="network unavailable")
    def test_fetch_timeout(self):
        from agentnodes.foundation.web.fetch import read_url
        result = read_url("https://httpbin.org/delay/10", timeout=1.0)
        body = json.loads(result)
        assert body["status"] == "error"

    @pytest.mark.skipif(not HAS_NETWORK, reason="network unavailable")
    def test_fetch_invalid_url(self):
        from agentnodes.foundation.web.fetch import read_url
        result = read_url("https://this-domain-does-not-exist-12345.com")
        body = json.loads(result)
        assert body["status"] == "error"

    @pytest.mark.skipif(not HAS_NETWORK, reason="network unavailable")
    def test_fetch_non_html(self):
        from agentnodes.foundation.web.fetch import read_url
        result = read_url("https://httpbin.org/robots.txt", max_chars=5000)
        body = json.loads(result)
        assert body["status"] == "ok"
        assert body["content_type"] != "text/html" or len(body["markdown"]) > 0

    def test_fetch_adds_scheme(self):
        from agentnodes.foundation.web.fetch import read_url
        result = read_url("httpbin.org/html", max_chars=5000)
        body = json.loads(result)
        # 应自动加 https://
        assert body["status"] in ("ok", "error")

    def test_fetch_json_format(self):
        from agentnodes.foundation.web.fetch import read_url
        # 测试空 URL 的格式
        result = read_url("")
        body = json.loads(result)
        assert "status" in body
        assert "error" in body

    @pytest.mark.skipif(not HAS_NETWORK, reason="network unavailable")
    def test_fetch_json_format_with_url(self):
        from agentnodes.foundation.web.fetch import read_url
        result = read_url("https://httpbin.org/html", max_chars=1000)
        body = json.loads(result)
        assert "status" in body
        assert "url" in body
        assert "markdown" in body
        assert "char_count" in body
        assert "truncated" in body

    @pytest.mark.skipif(not HAS_NETWORK, reason="network unavailable")
    def test_fetch_max_chars_configurable(self):
        from agentnodes.foundation.web.fetch import read_url
        result = read_url("https://httpbin.org/html", max_chars=50)
        body = json.loads(result)
        assert body["status"] == "ok"
        # 应该被截断
        if body["char_count"] > 50:
            assert body["truncated"] is True

    @pytest.mark.skipif(not HAS_NETWORK, reason="network unavailable")
    def test_fetch_redirect(self):
        from agentnodes.foundation.web.fetch import read_url
        result = read_url("https://httpbin.org/redirect-to?url=https://httpbin.org/html", max_chars=5000)
        body = json.loads(result)
        assert body["status"] == "ok"
