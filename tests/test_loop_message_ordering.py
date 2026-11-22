# -*- coding: utf-8 -*-
"""C4 回归：assistant→tool 消息顺序（MiniMax 400 根因，第四轮）。

``_make_strategy_ctx`` 浅拷贝 messages，LLMCall/ToolExecution 步骤往**副本**
append；调用方漏 rebind 时，发给 provider 的序列缺 assistant(tool_calls) 轮 →
严格校验的 provider 直接 400（``engine_adapters`` :90-97 原始记录）。

两引擎的 parity/equivalence 测试都不校验消息序列（MockLLM 不挑刺），本文件
用 **provider 校验 mock** 补上这一缺口：每个 tool result 前必须有携带对应
tool_call_id 的 assistant 轮——broken write-back 必红。subprocess 模式与
``test_loop_engine_equivalence.py`` 同款（骨架 async 引擎与 pytest 事件循环冲突）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_INNER = r'''
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"{root}\src")

from agentnodes.foundation.llm import LLMConfig, LLMResponse, ToolCall
from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.loop.core import AgentLoop


def _validate_provider_sequence(messages):
    """MiniMax 严格校验的最小形态：tool result 必须紧跟携带其
    tool_call_id 的 assistant 轮——否则 provider 400。"""
    for i, m in enumerate(messages):
        if m.get("role") != "tool":
            continue
        prev = messages[i - 1] if i else None
        assert prev is not None and prev.get("role") == "assistant", (
            "provider 400: tool result without preceding assistant turn; "
            f"roles={[x.get('role') for x in messages]}"
        )
        ids = {
            tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
            for tc in (prev.get("tool_calls") or [])
        }
        assert m.get("tool_call_id") in ids, (
            f"provider 400: tool_call_id {m.get('tool_call_id')!r} not in "
            f"preceding assistant tool_calls {ids}"
        )


class ValidatingLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.seen_sequences = 0

    def chat(self, messages, **kwargs):
        _validate_provider_sequence(messages)
        self.seen_sequences += 1
        if not self.responses:
            raise RuntimeError("ValidatingLLM exhausted")
        return self.responses.pop(0)

    async def achat(self, messages, **kwargs):
        return self.chat(messages, **kwargs)


def _build(ws, responses):
    loop = AgentLoop(
        stream_mode=False,
        config=LLMConfig(api_key="sk-test"),
        registry=build_default_registry(),
        workspace=ws,
        max_iterations=5,
        approval_timeout=1,
        approval_on_timeout="reject",
    )
    mock = ValidatingLLM(responses)
    loop.client.chat = mock.chat
    loop.client.achat = mock.achat
    return loop, mock


async def main():
    ws = Path(tempfile.mkdtemp())
    (ws / "strategies" / "foo").mkdir(parents=True)

    responses = json.loads(sys.argv[1])
    parsed = []
    for r in responses:
        tc_objs = [
            ToolCall(id=tc["id"], name=tc["name"], arguments=tc.get("arguments", {}))
            for tc in (r.get("tool_calls") or [])
        ]
        parsed.append(LLMResponse(
            content=r.get("content", ""),
            tool_calls=tc_objs,
            finish_reason=r.get("finish_reason", "stop"),
        ))

    async def _run_one(engine):
        loop, mock = _build(ws, list(parsed))
        result = await loop.arun("测试任务", engine=engine)
        return {
            "answer": result.answer,
            "success": result.success,
            "tool_calls_made": result.tool_calls_made,
            "provider_saw_sequences": mock.seen_sequences,
        }

    for engine in ("legacy", "skeleton"):
        out = await asyncio.wait_for(_run_one(engine), timeout=30)
        print(json.dumps({"engine": engine, **out}))

asyncio.run(main())
'''.replace("{root}", str(ROOT))


def _run_engine_scenario(responses: list, name: str) -> dict:
    payload = json.dumps(responses)
    r = subprocess.run(
        [sys.executable, "-c", _INNER, payload],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        timeout=120,
    )
    if r.returncode != 0:
        pytest.fail(f"{name} subprocess failed:\n{r.stdout}\n{r.stderr}")

    lines = [
        line for line in r.stdout.strip().splitlines()
        if line.startswith("{")
    ]
    assert len(lines) == 2, f"{name}: expected 2 JSON lines, got:\n{r.stdout}"
    legacy = json.loads(lines[0])
    skeleton = json.loads(lines[1])
    return legacy, skeleton


class TestProviderMessageOrdering:
    def test_tool_then_text_sequence_valid_for_both_engines(self):
        """tool→text 场景：两引擎发给 provider 的序列都必须过严格校验。

        broken write-back（漏 ``messages = llm_ctx.messages``）时 assistant
        轮丢在副本里 → provider 校验 assert 失败 → 本测试红。
        """
        responses = [
            {"content": "", "tool_calls": [
                {"id": "c1", "name": "list_history", "arguments": {}},
            ], "finish_reason": "tool_calls"},
            {"content": "工具跑完了", "tool_calls": [], "finish_reason": "stop"},
        ]
        legacy, skeleton = _run_engine_scenario(responses, "tool_then_text")

        assert legacy["success"] is True and legacy["tool_calls_made"] == 1
        assert skeleton["success"] is True and skeleton["tool_calls_made"] == 1
        # provider 真的收到了 >=2 轮请求（第一轮收到 user，第二轮收到
        # user+assistant(tool_calls)+tool_result）——序列校验在 mock 内生效
        assert legacy["provider_saw_sequences"] >= 2
        assert skeleton["provider_saw_sequences"] >= 2

    def test_pure_text_trivially_valid(self):
        """纯文本场景：无 tool result，校验平凡通过（mock 链路冒烟）。"""
        responses = [
            {"content": "最终答案", "tool_calls": [], "finish_reason": "stop"},
        ]
        legacy, skeleton = _run_engine_scenario(responses, "pure_text")
        assert legacy["success"] is True and skeleton["success"] is True
        assert skeleton["answer"] == "最终答案"


class TestStrategyCtxCopyContract:
    """C4 副本契约的直接单测（同步纯函数，无需 subprocess）。

    docstring 写明的两个语义此前零直接覆盖：

    - **副本隔离**：``ctx.messages`` 是输入列表的浅拷贝——步骤 append
      不泄漏回调用方列表；删掉 ``list(messages)``（"优化"）会让本测试红，
      这与 provider 校验回归互为两面（结构面 + 行为面）。
    - **budget 快照**：``tokens_used`` / ``cost_usd`` 在建 ctx 时刻取值，
      刻意陈旧——loop 字段事后变化不回传。
    """

    def _make_ctx(self, messages):
        from types import SimpleNamespace

        from agentnodes.kernel.agent.loop.core import _make_strategy_ctx

        loop = SimpleNamespace(_budget_tokens=42, _budget_cost_usd=1.5)
        result = SimpleNamespace(started_at=123.0)
        return (
            _make_strategy_ctx(loop, messages, None, result, 3, hook_ctx="h"),
            loop,
        )

    def test_ctx_messages_is_a_copy_not_the_input_list(self):
        messages = [{"role": "user", "content": "hi"}]
        ctx, _ = self._make_ctx(messages)

        assert ctx.messages == messages
        assert ctx.messages is not messages, "必须是副本（crash 隔离语义）"

        # 步骤视角：往 ctx append 不泄漏回调用方工作列表
        ctx.messages.append({"role": "assistant", "content": "a"})
        assert messages == [{"role": "user", "content": "hi"}]

        # 调用方视角：建 ctx 后原列表继续变化不回传
        ctx2, _ = self._make_ctx(messages)
        messages.append({"role": "tool", "tool_call_id": "c1"})
        assert ctx2.messages == [{"role": "user", "content": "hi"}]

    def test_budget_snapshot_is_stale_by_design(self):
        ctx, loop = self._make_ctx([{"role": "user"}])
        assert ctx.tokens_used == 42
        assert ctx.cost_usd == 1.5
        assert ctx.t0 == 123.0

        # 建_ctx 后记账字段变化不回传（快照语义——本轮 ctx 建于记账前）
        loop._budget_tokens = 100
        loop._budget_cost_usd = 9.9
        assert ctx.tokens_used == 42
        assert ctx.cost_usd == 1.5

    def test_ctx_carries_iteration_and_hook_ctx_identity(self):
        messages = [{"role": "user"}]
        ctx, _ = self._make_ctx(messages)
        assert ctx.task == ""
        assert ctx.iteration == 3
        assert ctx.hook_ctx == "h"
        assert ctx.should_stop is False
