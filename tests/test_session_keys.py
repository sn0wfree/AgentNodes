# -*- coding: utf-8 -*-
"""C6：session key 编解码单点的契约测试。

两 id 命名空间刻意不同：full session_key（mailbox 路由）vs fence domain =
物理 id（串行域/lease/cancel scope）。本文件钉住编解码的 round-trip、
多冒号、无前缀等边界——漂移会静默分裂 mailbox 或漏 fence。
"""
from __future__ import annotations

import pytest

from agentnodes.kernel.orchestration.session_keys import (
    make_session_key,
    physical_session_id,
)


class TestMakeSessionKey:
    def test_encodes_kind_prefix(self):
        assert make_session_key("chat", "s1") == "chat:s1"
        assert make_session_key("mcp", "client-a") == "mcp:client-a"
        assert make_session_key("automation", "nightly") == "automation:nightly"

    def test_round_trip(self):
        key = make_session_key("chat", "sess-42")
        assert physical_session_id(key) == "sess-42"

    def test_kind_with_colon_still_round_trips(self):
        """kind 段约定不含冒号（contracts 文法）；即便传了多冒号 kind，
        decode 只剥第一段——契约上由提交期校验拒绝，本单测钉行为。"""
        key = make_session_key("a:b", "x")
        assert key == "a:b:x"
        assert physical_session_id(key) == "b:x"


class TestPhysicalSessionId:
    @pytest.mark.parametrize("key,expected", [
        ("chat:x", "x"),
        ("automation:x", "x"),
        ("goal:x", "x"),
        ("mcp:client-a", "client-a"),
        ("a:b:c", "b:c"),          # 多冒号：剥一个 kind 段
        ("s1", "s1"),              # 无前缀：原样（scheduler 形态）
        ("chat:", ""),             # 空 name：剥出空串（提交期会被 MALFORMED 拒）
    ])
    def test_decode(self, key, expected):
        assert physical_session_id(key) == expected

    def test_cross_kind_same_domain(self):
        """R3 串行域不变量：chat:X 与 automation:X 同 fence 域。"""
        assert (
            physical_session_id("chat:x")
            == physical_session_id("automation:x")
            == "x"
        )
