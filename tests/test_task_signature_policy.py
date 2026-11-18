# -*- coding: utf-8 -*-
"""TaskSignaturePolicy 注入契约测试（顺序⑤ S1，K4）。

三个契约：
1. **默认行为等价**：不传 policy → 口径解析与签名计算与迁移前逐字一致；
2. **注入生效**：传自定义 policy → 签名按注入算法算；
3. **不变量保留**：签名**计算点仍在 store**——发起方无法自填签名
   （防奖励黑客：若发起方可自填，验证集可被挑选）。
"""
from __future__ import annotations

import pytest

from agentnodes.kernel.orchestration.task_signature import (
    TaskSignaturePolicy,
    default_compute_signature,
    resolve_signature_scope,
)
from agentnodes.kernel.orchestration.ticket_store import TicketStore


@pytest.fixture
def db(tmp_path):
    return tmp_path / "goals.db"


class TestDefaultBehaviourUnchanged:
    def test_default_policy_matches_resolved_scope(self, db):
        """不传 policy → role/tool_set 来自 resolve_signature_scope。"""
        role, tools = resolve_signature_scope()
        with TicketStore(db) as store:
            assert store.signature_role == role
            assert store.signature_tool_set == tools

    def test_default_compute_matches_holdout_implementation(self):
        """默认 compute = evolution.holdout 的同一实现。"""
        from agentnodes.kernel.evolution.holdout import compute_task_signature

        a = default_compute_signature("研究动量", "researcher", ("read", "write"))
        b = compute_task_signature("研究动量", "researcher", ("read", "write"))
        assert a == b

    def test_default_store_signature_non_empty(self, db):
        """默认路径能算出签名（迁移前后一致；签名落在 Assignment 上）。"""
        with TicketStore(db) as store:
            assignment = store.admit(
                session_key="chat:s1", session_id="s1", run_id="r1",
                turn_id="s1#1", objective="研究动量因子",
            )
            assert assignment.task_signature != ""


class TestPolicyInjection:
    def test_injected_policy_computes_custom_signature(self, db):
        """注入的 policy 决定签名算法（'怎么算'可替换）。"""
        calls: list[tuple] = []

        def fake(objective: str, role: str, tool_set: tuple[str, ...]) -> str:
            calls.append((objective, role, tool_set))
            return "CUSTOM-SIG"

        policy = TaskSignaturePolicy(
            role="custom-role", tool_set=("alpha", "beta"), compute=fake,
        )
        with TicketStore(db, signature_policy=policy) as store:
            assert store.signature_role == "custom-role"
            assert store.signature_tool_set == ("alpha", "beta")
            assignment = store.admit(
                session_key="chat:s1", session_id="s1", run_id="r1",
                turn_id="s1#1", objective="obj",
            )
            assert assignment.task_signature == "CUSTOM-SIG"
            assert calls, "注入的 compute 必须被调用"
            assert calls[0][1] == "custom-role"
            assert calls[0][2] == ("alpha", "beta")

    def test_injected_policy_scope_role(self):
        """TaskSignaturePolicy.from_scope() 走缺省口径解析。"""
        policy = TaskSignaturePolicy.from_scope()
        role, tools = resolve_signature_scope()
        assert policy.role == role
        assert policy.tool_set == tools

    def test_explicit_role_tool_set_still_supported(self, db):
        """旧参数 role/tool_set 仍然生效（向后兼容）。"""
        with TicketStore(db, role="r1", tool_set=("x", "y")) as store:
            assert store.signature_role == "r1"
            assert store.signature_tool_set == ("x", "y")


class TestAntiGamingInvariant:
    def test_caller_cannot_supply_signature(self, db):
        """发起方不得自填签名——admit 无 signature 参数（防奖励黑客）。"""
        import inspect

        params = inspect.signature(TicketStore.admit).parameters
        assert "signature" not in params, (
            "admit() 不得接受调用方提供的签名（防 holdout 被挑选）"
        )
        assert "task_signature" not in params

    def test_signature_computed_inside_store(self, db):
        """签名计算点在 store 内部（不变量：唯一合法计算点）。"""
        import ast
        import inspect
        import textwrap

        from agentnodes.kernel.orchestration import ticket_store as ts_mod

        src = inspect.getsource(ts_mod.TicketStore._signature_for)
        assert "compute_signature" in src, (
            "_signature_for 必须经 policy 计算（计算点留在 store）"
        )
        # 不再有 evolution 的 import 语句（算法已注入，延迟 import 移出）。
        # （docstring 里提及 kernel.evolution 是说明文字，不算依赖。）
        tree = ast.parse(textwrap.dedent(src))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "kernel.evolution" not in (node.module or ""), (
                    "_signature_for 不得直接 import evolution（策略已注入）"
                )
