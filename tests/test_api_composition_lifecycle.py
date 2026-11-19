# -*- coding: utf-8 -*-
"""M12 后续（17:06 复审候选 1）：composition 的关闭与释放同一生命周期。

复现基线：register() 为每个容器注册绑定容器的 atexit 回调；close_all()
只清 _REGISTRY，回调仍持有容器强引用 → 测试 reset / 多次 app 构造后
对象无法回收。先红后绿。

测试用 monkeypatch 把 atexit.register 换成记录器（避免真注册污染进程），
断言回调只能是「不持有容器的 close_all」——若仍绑定容器则判红。
"""
from __future__ import annotations

import gc
import weakref

from agentnodes.interfaces.api import composition


class _FakeResources:
    def __init__(self, tag="r"):
        self.db_path = f"/tmp/{tag}"
        self.closed = 0

    def close(self):
        self.closed += 1


def _reset(monkeypatch):
    recorded = []

    def _record(fn, *args):
        recorded.append((fn, args))

    monkeypatch.setattr(composition.atexit, "register", _record)
    composition._REGISTRY.clear()
    composition._CLOSED.clear()
    monkeypatch.setattr(composition, "_ATEXIT_REGISTERED", False, raising=False)
    return recorded


def test_close_is_idempotent(monkeypatch):
    _reset(monkeypatch)
    calls = []
    monkeypatch.setattr(
        "agentnodes.interfaces.api.container.close_container",
        lambda container: calls.append(container),
    )
    r = _FakeResources()
    composition.close_resources(r)
    composition.close_resources(r)
    assert calls == [r], "close_container 恰调一次（幂等）"


def test_registry_cleared_on_close_and_unregister(monkeypatch):
    """close_all / unregister 后注册表清空——回收的注册表侧条件。"""
    _reset(monkeypatch)
    r = _FakeResources("release")
    composition.register(r)
    composition.close_all()
    assert composition._REGISTRY == {}
    composition.register(r)
    composition.unregister(r)
    assert composition._REGISTRY == {}


def test_weakref_released_when_no_atexit_binding(monkeypatch):
    """注册表本身不持有已 close 的容器（用弱引用观察）。"""
    _reset(monkeypatch)
    r = _FakeResources("weak")
    ref = weakref.ref(r)
    composition.register(r)
    composition.close_all()  # 应清注册表 + 释放引用
    del r
    gc.collect()
    assert ref() is None, "注册表仍持有已关闭容器"


def test_atexit_registered_once_without_container(monkeypatch):
    """核心：atexit 只注册一次，且回调是无参 close_all（不绑定容器）。"""
    recorded = _reset(monkeypatch)
    composition.register(_FakeResources("a"))
    composition.register(_FakeResources("b"))
    assert len(recorded) == 1, "atexit 回调应只注册一次（当前实现每容器一次 → 红）"
    fn, args = recorded[0]
    assert fn is composition.close_all and not args, "兜底入口必须是无参 close_all（不绑定容器）"
