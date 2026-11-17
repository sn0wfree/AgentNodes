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
from agentnodes.kernel.agent.loop.core import AgentLoop


class MockLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, **kwargs):
        if not self.responses:
            raise RuntimeError("MockLLM exhausted")
        return self.responses.pop(0)

    async def achat(self, messages, **kwargs):
        return self.chat(messages, **kwargs)


def _build(ws, responses, extra=None):
    kw = dict(
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
    if extra:
        # 预算是 LoopConfig 的字段（不是 AgentLoop 构造参数）——
        # 经 strategy spec 传入，两引擎读同一个 strategy.config
        kw["strategy"] = {"name": "react", "config": dict(extra)}
    loop = AgentLoop(**kw)
    mock = MockLLM(responses)
    loop.client.chat = mock.chat
    loop.client.achat = mock.achat
    return loop


async def main():
    ws = Path(tempfile.mkdtemp())
    (ws / "strategies" / "foo").mkdir(parents=True)

    responses = json.loads(sys.argv[1])
    extra = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
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
        legacy = _build(ws, list(parsed), extra)
        lr = await legacy.arun("测试任务", engine="legacy")
        skel = _build(ws, list(parsed), extra)
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


def _run_scenario(responses: list, name: str, extra: dict | None = None) -> tuple[dict, dict]:
    """Run both engines in a subprocess, parse two JSON lines."""
    payload = json.dumps(responses)
    extra_payload = json.dumps(extra or {})
    r = subprocess.run(
        [sys.executable, "-c", _INNER, payload, extra_payload],
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
    @pytest.mark.parametrize("scenario,responses,extra", [
        ("pure_text",
         [{"content": "最终答案", "tool_calls": [], "finish_reason": "stop"}],
         None),
        ("tool_then_text", [
            {"content": "", "tool_calls": [{"id": "c1", "name": "list_history", "arguments": {}}], "finish_reason": "tool_calls"},
            {"content": "工具跑完了", "tool_calls": [], "finish_reason": "stop"},
        ], None),
        # 批次 12 S1 新增场景 —— 翻默认前的边界覆盖
        ("tool_error", [
            # read 不存在的文件 → tool 返回 error（不是异常），两引擎应同判
            {"content": "", "tool_calls": [{"id": "c1", "name": "read", "arguments": {"path": "nope.txt"}}], "finish_reason": "tool_calls"},
            {"content": "文件不存在", "tool_calls": [], "finish_reason": "stop"},
        ], None),
        ("budget_exhausted",
         [{"content": "预算内", "tool_calls": [], "finish_reason": "stop"}],
         {"budget_max_seconds": 0.0}),
        ("history_multi_turn", [
            {"content": "第二轮答案", "tool_calls": [], "finish_reason": "stop"},
        ], None),
    ])
    def test_engines_agree(self, scenario, responses, extra):
        legacy, skeleton = _run_scenario(responses, scenario, extra)
        # stop_reason 措辞允许不同（skeleton 用 decider 语义标签如
        # "final_text"，legacy 用 None/_handle_stop 的 reason）——
        # 核心断言：answer / finished_reason / iterations / tool_calls 一致
        for key in ("answer", "finished_reason", "iterations", "tool_calls_made", "success"):
            assert legacy[key] == skeleton[key], (
                f"{scenario}.{key}: legacy={legacy[key]!r} skeleton={skeleton[key]!r}"
            )

    def test_repetitive_tool_calls_converge(self):
        """重复 tool_call：**单引擎绝对值**断言（U1 转正，R-009 反对偶）。

        原对偶形式（legacy == skeleton）在 U6 删 legacy 后会变成
        "自己跟自己比"。按统一设计 §4/§6：行为断言转为对 skeleton 的
        绝对值，legacy 对照值改为**显式 engine="legacy" 钉住**（遗产网
        形态，U6 时随 legacy 一起删）。

        历史注释勘误：本文件曾记载"skeleton 直接 EXIT 落 stop"——批次 12
        S1 已把 approval gate 收编进 NoProgressDecider，两引擎现在**都**
        走审批闸、都落 ``approval_timeout_rejected``（子进程无用户 → 1s
        超时 reject）。exit-net（test_loop_exit_net.py）比事件序列，
        这里比结果字段，两层互补。
        """
        responses = [
            {"content": "", "tool_calls": [{"id": f"c{i}", "name": "list_history", "arguments": {}}], "finish_reason": "tool_calls"}
            for i in range(5)
        ]
        legacy, skeleton = _run_scenario(responses, "repetitive")
        # skeleton（统一后的唯一引擎）：收敛 + 精确语义
        assert skeleton["iterations"] < 5, f"skeleton ran to max: {skeleton}"
        assert skeleton["finished_reason"] == "approval_timeout_rejected", (
            f"skeleton 语义漂移: {skeleton}"
        )
        # legacy 对照值（遗产网形态：engine 显式钉住；两引擎经同一审批闸，
        # 该字段应一致——若分叉，说明闸的行为改了，必须查）
        assert legacy["finished_reason"] == skeleton["finished_reason"], (
            f"approval gate 语义分叉: legacy={legacy['finished_reason']} "
            f"skeleton={skeleton['finished_reason']}"
        )

    def test_skeleton_final_text(self):
        """骨架单独冒烟：纯文本 → answer 非空 + success（绝对值，不依赖 legacy）。"""
        legacy, skeleton = _run_scenario(
            [{"content": "答案", "tool_calls": [], "finish_reason": "stop"}],
            "smoke",
        )
        assert skeleton["answer"] == "答案"
        assert skeleton["success"] is True
