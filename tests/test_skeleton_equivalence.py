"""批次 11 · B3 阶段 1 — 骨架与 legacy 引擎的等价证明。

**subprocess 模式**（与 ``test_kernel_standalone.py`` 同一模式）——
骨架是 async 引擎，pytest 的事件循环管理与 ``asyncio.run()`` 冲突
（实测挂起）。subprocess 隔离后裸 Python 跑通（已验证）。

每个场景一个子进程：先 legacy 后 skeleton，逐字段比对 LoopResult。
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
from agentnodes.kernel.agent.loop import AgentLoop


class MockLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, **kwargs):
        if not self.responses:
            raise RuntimeError("MockLLM exhausted")
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
        # no-progress approval gate：子进程无用户 → 1s 超时即 reject，
        # 与骨架 NoProgressDecider 的立即 EXIT 对齐（否则 legacy 等 30 分钟）
        approval_timeout=1,
        approval_on_timeout="reject",
    )
    mock = MockLLM(responses)
    loop.client.chat = mock.chat
    loop.client.achat = mock.achat
    return loop


async def main():
    ws = Path(tempfile.mkdtemp())
    (ws / "strategies" / "foo").mkdir(parents=True)

    responses = json.loads(sys.argv[1])
    # JSON dicts → LLMResponse objects (tool_calls → ToolCall)
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

    async def _run_both():
        legacy = _build(ws, list(parsed))
        lr = await legacy.arun("测试任务", engine="legacy")
        skel = _build(ws, list(parsed))
        sr = await skel.arun("测试任务", engine="skeleton")
        return lr, sr

    lr, sr = await asyncio.wait_for(_run_both(), timeout=30)
    for r in (lr, sr):
        print(json.dumps({
            "answer": r.answer,
            "stop_reason": r.stop_reason,
            "finished_reason": r.finished_reason,
            "iterations": r.iterations,
            "tool_calls_made": r.tool_calls_made,
            "success": r.success,
        }))

asyncio.run(main())
'''.replace("{root}", str(ROOT))


def _run_scenario(responses: list, name: str) -> tuple[dict, dict]:
    """Run both engines in a subprocess, parse two JSON lines."""
    payload = json.dumps(responses)
    r = subprocess.run(
        [sys.executable, "-c", _INNER, payload, name],
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
    if len(lines) != 2:
        pytest.fail(f"{name}: expected 2 JSON lines, got {len(lines)}:\n{r.stdout}")
    legacy = json.loads(lines[0])
    skeleton = json.loads(lines[1])
    return legacy, skeleton


class TestEquivalence:
    @pytest.mark.parametrize("scenario,responses", [
        ("pure_text", [{"content": "最终答案", "tool_calls": [], "finish_reason": "stop"}]),
        ("tool_then_text", [
            {"content": "", "tool_calls": [{"id": "c1", "name": "list_history", "arguments": {}}], "finish_reason": "tool_calls"},
            {"content": "工具跑完了", "tool_calls": [], "finish_reason": "stop"},
        ]),
    ])
    def test_engines_agree(self, scenario, responses):
        legacy, skeleton = _run_scenario(responses, scenario)
        # stop_reason 措辞允许不同（skeleton 用 decider 语义标签如
        # "final_text"，legacy 用 None/_handle_stop 的 reason）——
        # 核心断言：answer / finished_reason / iterations / tool_calls 一致
        for key in ("answer", "finished_reason", "iterations", "tool_calls_made", "success"):
            assert legacy[key] == skeleton[key], (
                f"{scenario}.{key}: legacy={legacy[key]!r} skeleton={skeleton[key]!r}"
            )

    def test_repetitive_tool_calls_converge(self):
        """重复 tool_call：两引擎都收敛（不是跑满 5 轮）。

        **阶段 1 已知差异（设计文档 §「已知差异」）**：

        - legacy：no-progress 触发后走 **approval gate**（子进程
          approval_timeout=1 → reject），finished_reason 落
          ``approval_timeout_rejected``
        - skeleton：NoProgressDecider 直接 EXIT，finished_reason 落 ``stop``

        **两者都收敛**（核心一致性），措辞差异来自骨架尚未收编 approval
        gate（B3 阶段 2 的工作，届时统一）。本测试断言**收敛**这一本质，
        不锁具体措辞——锁措辞会把阶段 1 的已知收窄变成回归噪音。
        """
        responses = [
            {"content": "", "tool_calls": [{"id": f"c{i}", "name": "list_history", "arguments": {}}], "finish_reason": "tool_calls"}
            for i in range(5)
        ]
        legacy, skeleton = _run_scenario(responses, "repetitive")
        # 都收敛（非跑满 max_iterations=5）
        assert legacy["iterations"] < 5, f"legacy ran to max: {legacy}"
        assert skeleton["iterations"] < 5, f"skeleton ran to max: {skeleton}"
        # 都产生了终止信号（非 error、非空）
        assert legacy["finished_reason"] and skeleton["finished_reason"]
    def test_skeleton_final_text(self):
        """骨架单独冒烟：纯文本 → answer 非空 + success。"""
        legacy, skeleton = _run_scenario(
            [{"content": "答案", "tool_calls": [], "finish_reason": "stop"}],
            "smoke",
        )
        assert skeleton["answer"] == "答案"
        assert skeleton["success"] is True
