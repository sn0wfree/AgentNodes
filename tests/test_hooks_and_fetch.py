"""H 档补测 —— `core/hooks/composite.py` 与 `core/web/fetch.py`。

两个小文件的剩余缺口：
- CompositeHook：把一个 hook 列表当作一个 hook 转发事件。缺口在**错误隔离**
  （单个 hook 抛异常不得拖垮其余）与**管道方法**（finalize_content 的
  返回值链式传递，异常则上抛——与 _fire 的吞掉策略相反）。
- read_url：URL 规整、非 HTML 直接返回、HTML→Markdown、截断标记、异常降级。
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from strategy_research.core.hooks.composite import (
    AgentHook,
    CompositeHook,
    NoOpHook,
)
from strategy_research.core.hooks.context import AgentHookContext
from strategy_research.core.web import fetch as web_fetch


# ── CompositeHook ───────────────────────────────────────────


class _Recorder(AgentHook):
    def __init__(self, name: str, *, raise_on: str | None = None) -> None:
        self.name = name
        self.calls: list[str] = []
        self._raise_on = raise_on

    async def before_iteration(self, ctx: AgentHookContext) -> None:
        self.calls.append("before_iteration")
        if self._raise_on == "before_iteration":
            raise RuntimeError(f"{self.name} boom")

    async def after_iteration(self, ctx: AgentHookContext) -> None:
        self.calls.append("after_iteration")

    async def finalize_content(self, ctx: AgentHookContext,
                              content: str | None) -> str | None:
        self.calls.append(f"finalize:{content}")
        if self._raise_on == "finalize":
            raise RuntimeError(f"{self.name} boom")
        return f"{content}+{self.name}"

    def wants_streaming(self) -> bool:
        return self.name == "streaming"


def _ctx() -> AgentHookContext:
    return AgentHookContext()


def test_composite_len_and_mutation():
    a, b = _Recorder("a"), _Recorder("b")
    comp = CompositeHook([a, b])
    assert len(comp) == 2
    comp.add(_Recorder("c"))
    assert len(comp) == 3
    comp.remove("b")
    assert len(comp) == 2
    assert all(h.name != "b" for h in comp._hooks)
    comp.clear()
    assert len(comp) == 0


def test_composite_empty_is_noop():
    comp = CompositeHook()
    import asyncio
    asyncio.run(comp.before_iteration(_ctx()))   # 不抛


def test_composite_fans_out_to_all():
    a, b = _Recorder("a"), _Recorder("b")
    comp = CompositeHook([a, b])
    import asyncio
    asyncio.run(comp.before_iteration(_ctx()))
    assert a.calls == ["before_iteration"]
    assert b.calls == ["before_iteration"]


def test_composite_isolates_hook_failure():
    """单个 hook 抛异常 → 其余照常收到事件（_fire 吞掉并记警告）。"""
    bad = _Recorder("bad", raise_on="before_iteration")
    good = _Recorder("good")
    comp = CompositeHook([bad, good])
    import asyncio
    asyncio.run(comp.before_iteration(_ctx()))
    assert good.calls == ["before_iteration"]


def test_composite_finalize_pipeline_chains_results():
    """finalize_content 是管道：每个 hook 收到上一个的输出。"""
    a, b = _Recorder("a"), _Recorder("b")
    comp = CompositeHook([a, b])
    import asyncio
    out = asyncio.run(comp.finalize_content(_ctx(), "seed"))
    assert out == "seed+a+b"


def test_composite_finalize_keeps_value_when_hook_returns_none():
    class Passthrough(AgentHook):
        name = "pass"

        async def finalize_content(self, ctx, content):
            return None

    comp = CompositeHook([Passthrough()])
    import asyncio
    assert asyncio.run(comp.finalize_content(_ctx(), "seed")) == "seed"


def test_composite_finalize_propagates_failure():
    """管道方法相反：异常上抛（内容被改坏不能静默放过）。"""
    bad = _Recorder("bad", raise_on="finalize")
    comp = CompositeHook([bad])
    import asyncio
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(comp.finalize_content(_ctx(), "seed"))


def test_composite_wants_streaming_any():
    assert CompositeHook().wants_streaming() is False
    assert CompositeHook([NoOpHook()]).wants_streaming() is False
    assert CompositeHook([NoOpHook(), _Recorder("streaming")]).wants_streaming() is True


def test_noop_hook_accepts_everything():
    """NoOpHook 继承基类的空实现（同步 pass），调用不抛即可。"""
    hook = NoOpHook()
    hook.before_iteration(_ctx())
    hook.after_iteration(_ctx())
    hook.on_stream(_ctx(), "x")
    assert hook.wants_streaming() is False


# ── read_url ────────────────────────────────────────────────


def test_read_url_empty_returns_error():
    out = json.loads(web_fetch.read_url(""))
    assert out["status"] == "error"
    assert "url is required" in out["error"]
    assert json.loads(web_fetch.read_url("   "))["status"] == "error"


def test_read_url_adds_https_scheme(monkeypatch):
    seen: dict[str, Any] = {}

    class Resp:
        headers = {"content-type": "application/json"}
        text = '{"ok": true}'

        def raise_for_status(self):
            pass

    def fake_get(url, **kwargs):
        seen["url"] = url
        return Resp()

    monkeypatch.setattr(web_fetch.httpx, "get", fake_get)
    web_fetch.read_url("example.com/data")
    assert seen["url"] == "https://example.com/data"


def test_read_url_keeps_explicit_http(monkeypatch):
    seen: dict[str, Any] = {}

    class Resp:
        headers = {"content-type": "text/plain"}
        text = "hi"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(web_fetch.httpx, "get",
                        lambda url, **kw: (seen.update(url=url), Resp())[1])
    web_fetch.read_url("http://example.com")
    assert seen["url"] == "http://example.com"


def test_read_url_non_html_returned_as_is(monkeypatch):
    class Resp:
        headers = {"content-type": "application/json"}
        text = '{"k": 1}'

        def raise_for_status(self):
            pass

    monkeypatch.setattr(web_fetch.httpx, "get", lambda url, **kw: Resp())
    out = json.loads(web_fetch.read_url("https://x/data.json"))
    assert out["status"] == "ok"
    assert out["markdown"] == '{"k": 1}'
    assert out["truncated"] is False


def test_read_url_truncates_non_html(monkeypatch):
    class Resp:
        headers = {"content-type": "text/plain"}
        text = "a" * 500

        def raise_for_status(self):
            pass

    monkeypatch.setattr(web_fetch.httpx, "get", lambda url, **kw: Resp())
    out = json.loads(web_fetch.read_url("https://x/big.txt", max_chars=100))
    assert out["truncated"] is True
    assert "[truncated]" in out["markdown"]
    assert out["char_count"] <= 120


def test_read_url_html_converted(monkeypatch):
    class Resp:
        headers = {"content-type": "text/html; charset=utf-8"}
        text = "<html><body><h1>Title</h1><p>Body text</p></body></html>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(web_fetch.httpx, "get", lambda url, **kw: Resp())
    out = json.loads(web_fetch.read_url("https://x/page"))
    assert out["status"] == "ok"
    assert "Title" in out["markdown"]
    assert "Body text" in out["markdown"]


def test_read_url_http_error_degrades(monkeypatch):
    """HTTP 错误 → 返回 error JSON（不抛）。"""
    class Resp:
        headers: dict = {}

        def raise_for_status(self):
            raise RuntimeError("500 server error")

    monkeypatch.setattr(web_fetch.httpx, "get", lambda url, **kw: Resp())
    out = json.loads(web_fetch.read_url("https://x/boom"))
    assert out["status"] == "error"


def test_read_url_network_exception_degrades(monkeypatch):
    def boom(url, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(web_fetch.httpx, "get", boom)
    out = json.loads(web_fetch.read_url("https://x/boom"))
    assert out["status"] == "error"
    assert out["url"] == "https://x/boom"
