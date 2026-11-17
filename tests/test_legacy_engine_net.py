"""legacy 引擎遗产网（统一引擎 U5 · D8 形态）。

**为什么需要**：U5 把 ``run()``（sync）从 legacy 切到统一引擎后，测试里
那些**依赖 legacy 特有行为**的断言会失效。按 D8 规则处置：

- **引擎无关**断言（answer / success / iterations）→ 原地保留，两引擎都该满足；
- **legacy 特有**断言 → 迁到本文件，用 ``arun(engine="legacy")`` **显式**钉住。

**生存期**：U5 建立 → U6（删 legacy）时随 legacy 一起删。

**本文件钉住的 legacy 特有行为**（统一引擎**故意**不同——D7：统一是超集）：

| 行为 | legacy | 统一引擎（skeleton）| 依据 |
|---|---|---|---|
| 异常收尾发 ``loop_end``/``iter_end`` | **不发** | **发**（LoopFinalize 走 FINALIZE 漏斗，U1）| inventory §6 #1；V4-1 实测 |
| ``budget_exhausted`` 事件 | **发** | **发**（U2 已补发）| inventory §6 #2 → U2 后已一致 |
| ``stop_reason`` 措辞 | None / `_handle_stop` reason | decider 语义标签（如 ``final_text``/``no_progress``）| inventory §6 #3 |
| ``loop_final``（metrics）| 异常路径不发 | 一律发（FINALIZE 跑 metrics）| U1 |

**注意**：凡已在 U1-U4 补齐的行为（``budget_exhausted``、EXIT 收尾）**不再**
是差异项——本文件只钉**仍然不同**的那些，避免把已统一的行为重新钉成"差异"。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: 与 test_skeleton_equivalence 同一 subprocess 模式——骨架是 async 引擎，
#: pytest 的事件循环管理与 ``asyncio.run()`` 冲突（实测挂起）。
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

USAGE = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


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
        approval_timeout=1,
        approval_on_timeout="reject",
    )
    if extra:
        kw["strategy"] = {"name": "react", "config": dict(extra)}
    loop = AgentLoop(**kw)
    mock = MockLLM(responses)
    loop.client.chat = mock.chat
    loop.client.achat = mock.achat
    return loop


async def main():
    ws = Path(tempfile.mkdtemp())
    (ws / "strategies" / "foo").mkdir(parents=True)
    events = []
    parsed = [LLMResponse(
        content=r.get("content", ""),
        tool_calls=[ToolCall(id=tc["id"], name=tc["name"],
                             arguments=tc.get("arguments", {}))
                    for tc in (r.get("tool_calls") or [])],
        finish_reason=r.get("finish_reason", "stop"),
        usage=USAGE,
    ) for r in json.loads(sys.argv[1])]

    engine = sys.argv[2]
    extra = json.loads(sys.argv[3]) if len(sys.argv) > 3 else None
    loop = _build(ws, list(parsed), extra)
    loop._on_event = lambda kind, payload=None, *a, **k: events.append(kind)
    r = await asyncio.wait_for(
        loop.arun("测试任务", engine=engine), timeout=60,
    )
    print("JSONOUT" + json.dumps({
        "engine": engine,
        "answer": r.answer,
        "stop_reason": r.stop_reason,
        "finished_reason": r.finished_reason,
        "iterations": r.iterations,
        "success": r.success,
        "events": events,
    }, ensure_ascii=False))

asyncio.run(main())
'''.replace("{root}", str(ROOT))


def _run(responses: list, engine: str, extra: str | None = None) -> dict:
    """跑指定引擎，返回 result 字段 + 事件列表。

    ``extra`` 是 strategy config 的 JSON 串（预算等维度）——传给子进程作
    第二个 argv。
    """
    payload = json.dumps(responses)
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, "-c", _INNER, payload, engine, extra or "{}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=180,
    )
    if r.returncode != 0:
        pytest.fail(f"{engine} subprocess failed:\n{r.stdout}\n{r.stderr}")
    lines = [l for l in r.stdout.strip().splitlines() if l.startswith("JSONOUT")]
    if len(lines) != 1:
        pytest.fail(f"{engine}: expected 1 JSON line, got {len(lines)}:\n{r.stdout}")
    return json.loads(lines[0][7:])


# ── 差异 1：异常收尾不发 loop_end / iter_end（legacy 特有）──


def test_legacy_error_exit_does_not_emit_end_events():
    """legacy 异常收尾**不发** loop_end/iter_end；统一引擎发（D7 超集）。

    这条断言在 U6 删 legacy 后**随本文件一起删**。
    """
    # 第二轮 LLM 抛异常（MockLLM exhausted）→ legacy 异常收尾
    responses = [
        {"content": "", "tool_calls": [{"id": "c1", "name": "list_history",
                                        "arguments": {}}],
         "finish_reason": "tool_calls"},
    ]
    got = _run(responses, "legacy")
    assert "loop_end" not in got["events"], (
        f"legacy 异常收尾不应发 loop_end（若统一后 legacy 也发了，"
        f"这条差异项可删）：{got['events']}"
    )
    assert "iter_end" not in got["events"], got["events"]
    assert got["finished_reason"] == "error", got


def test_unified_error_exit_does_emit_end_events():
    """统一引擎（skeleton）异常收尾**发** loop_end/iter_end（D7 超集）。"""
    responses = [
        {"content": "", "tool_calls": [{"id": "c1", "name": "list_history",
                                        "arguments": {}}],
         "finish_reason": "tool_calls"},
    ]
    got = _run(responses, "skeleton")
    assert "loop_end" in got["events"], got["events"]
    assert "iter_end" in got["events"], got["events"]


# ── 差异 2：stop_reason 措辞（legacy None vs 统一 decider 标签）──


def test_stop_reason_wording_differs():
    """legacy 的 stop_reason 是 None；统一引擎用 decider 语义标签。"""
    responses = [{"content": "答案", "tool_calls": [], "finish_reason": "stop"}]
    legacy = _run(responses, "legacy")
    unified = _run(responses, "skeleton")
    assert legacy["stop_reason"] is None, (
        f"legacy stop_reason 应为 None（若已统一则本差异项可删）："
        f"{legacy['stop_reason']!r}"
    )
    assert unified["stop_reason"] is not None, (
        f"统一引擎 stop_reason 应有值：{unified}"
    )


# ── 引擎无关：两引擎都该满足（此处钉住，防止遗产网变成"差异收藏"）──


def test_engine_agnostic_behavior_same():
    """引擎无关行为：两引擎**必须**一致（不是差异项）。"""
    responses = [{"content": "答案", "tool_calls": [], "finish_reason": "stop"}]
    legacy = _run(responses, "legacy")
    unified = _run(responses, "skeleton")
    for key in ("answer", "finished_reason", "iterations", "success"):
        assert legacy[key] == unified[key], (
            f"{key}: legacy={legacy[key]!r} unified={unified[key]!r}"
        )


# ── budget_exhausted：U2 后两引擎已一致（**不再是**差异项）──


def test_budget_exhausted_now_consistent():
    """U2 补发后两引擎都发 budget_exhausted——**已不是**差异项。

    这条留在遗产网是**反向标记**：证明差异项已消解，U6 时可整条删除。

    场景：每响应 150 token（USAGE），上限 100 → 首轮即超预算。
    """
    responses = [{"content": "预算内", "tool_calls": [], "finish_reason": "stop"}]
    extra = json.dumps({"budget_max_tokens": 100})
    legacy = _run(responses, "legacy", extra=extra)
    unified = _run(responses, "skeleton", extra=extra)
    assert "budget_exhausted" in legacy["events"], legacy["events"]
    assert "budget_exhausted" in unified["events"], unified["events"]
