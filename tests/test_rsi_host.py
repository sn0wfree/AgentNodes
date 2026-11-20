# -*- coding: utf-8 -*-
"""候选 6：RsiHost 宿主适配器单测——注入契约单点、两宿主共用。

此前 CLI 命令与 goal_settled_consumer 逐字复制 halt_check + builder 组装；
现在只调 build_rsi_host().run_cycle(...)。本文件钉住：runner 已绑定
halt/loop、调用方只传业务参数、两宿主确实走同一入口。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentnodes.interfaces.rsi_host import RsiHost, build_rsi_host


class FakeOutcome:
    def __init__(self, reason="ran"):
        self.reason = reason
        self.ran = True
        self.ok = True
        self.detail = {}


class TestRsiHost:
    def test_run_cycle_binds_injection_contract(self):
        """RsiHost.run_cycle 把已绑定的 halt/builder 传给 run_once，调用方只传业务参。"""
        captured = {}

        def fake_run_once(**kw):
            captured.update(kw)
            return FakeOutcome()

        halt = lambda: (False, "")
        builder = lambda gw, emit, seed: object()
        host = RsiHost(fake_run_once, halt, builder)

        out = host.run_cycle(session_id="s1", seed=7, force=True)
        assert out.ran is True
        assert captured["session_id"] == "s1"
        assert captured["seed"] == 7
        assert captured["force"] is True
        assert captured["halt_check"] is halt
        assert captured["loop_builder"] is builder

    def test_build_rsi_host_wires_real_assets(self, monkeypatch):
        """build_rsi_host 装配真实 halt/builder（单点实现，不依赖具体宿主）。"""
        import agentnodes.interfaces.cli.halt as halt_mod
        import agentnodes.interfaces.rsi_wiring as wiring

        monkeypatch.setattr(halt_mod, "is_halted", lambda: False)
        monkeypatch.setattr(wiring, "build_rsi_loop", lambda *a, **kw: object())

        host = build_rsi_host()
        assert isinstance(host, RsiHost)
        assert host._halt_check() == (False, "")
        assert callable(host._loop_builder)

    def test_both_hosts_use_single_entry(self):
        """架构锚：两宿主的源码都调 build_rsi_host，不再复制注入细节。"""
        import io
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src" / "agentnodes" / "interfaces"
        for rel in ("cli/commands/rsi.py", "goal_settled_consumer.py"):
            text = io.open(root / rel, encoding="utf-8").read()
            assert "build_rsi_host" in text, rel
            assert "halt_check=_halt_check" not in text, rel  # 旧复制形态已消失
            assert "make_loop_builder" not in text, rel
