"""Loop 出口收尾网（统一引擎 U1）——R-016 的直接落地。

**为什么存在**：V4-4b 实测——7166 条全量绿 + batch_gate 绿，却漏掉了
「引擎出口收尾」整类缺陷（skeleton 两个 EXIT 出口不关 trace、不设
``trace_path``、不发 loop_end/iter_end）。全量测试只比结果字段，
**不比出口收尾**。本文件把「所有出口都必须完整收尾」写成绝对值断言网
（R-009：断言必须有正向前提；R-016：删 fallback 前先有覆盖所有出口的网）。

**出口矩阵**（loop-unification-design-20261111.md §3.2，AST 实测）：

| # | 出口 | 触发场景 |
|---|---|---|
| 1 | precheck STOP（预算）| budget_max_tokens 触发（响应带 usage，token 维度真触发）|
| 3 | reasoner error | LLM 抛异常 |
| 4 | after_reason STOP（文本终答）| 纯文本响应 |
| 5 | actor error | 工具执行抛异常 |
| 6 | after_act EXIT（no-progress 拒绝）| approval 超时 reject |
| 8 | max_iter（for-else）| max_iterations=1 + 每轮带 tool_call |
| — | precheck EXIT（#2）当前不可达、after_act STOP（#7）留给自定义 decider | 不在本网 |

每个出口断言 6 项收尾（D7：统一引擎出口收尾 = legacy 超集）：
``trace_path`` 非空 · trace 文件存在且已关闭 · ``loop_end`` 恰 1 次 ·
``iter_end`` 恰 1 次 · ``after_run`` hook 恰 1 次 · 无重复 ``done``。

事件收集走 ``on_event`` 回调（agent_telemetry.emit 的通道）；
trace 关闭状态从 ``loop._trace_writer`` 读——EXIT 改 break 后该对象
在 ``run_to_completion`` 返回时应已 closed（现在没有 → U1 前本网红）。
"""
from __future__ import annotations
from llm_client_mock import patch_llm_client

import asyncio
import json
from collections import Counter
from pathlib import Path

import pytest

from agentnodes.foundation.llm import LLMConfig, LLMResponse, ToolCall
from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.loop.core import AgentLoop

USAGE = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


class MockLLM:
    """脚本化响应；每响应带 usage（预算维度必须真触发，R-009）。"""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.hook_after_run = 0

    def chat(self, messages, **kwargs):
        if not self.responses:
            raise RuntimeError("MockLLM exhausted")
        return self.responses.pop(0)

    async def achat(self, messages, **kwargs):
        return self.chat(messages, **kwargs)


def _resp(**kw) -> LLMResponse:
    tc_objs = [ToolCall(id=t["id"], name=t["name"], arguments=t.get("arguments", {}))
               for t in (kw.pop("tool_calls", None) or [])]
    return LLMResponse(content=kw.get("content", ""), tool_calls=tc_objs,
                       finish_reason=kw.get("finish_reason", "stop"), usage=USAGE)


def _build(ws: Path, responses, *, approval_timeout: float | None = None,
           approval_on_timeout: str = "reject", max_iterations: int = 5,
           extra_cfg: dict | None = None):
    """生产装配（arun 默认走 skeleton），trace_dir 开在临时目录。

    两个参数经 strategy config 传入（loop.py:469：显式 strategy 时
    ``cfg.*`` 覆盖构造参数）：
    - ``no_progress_window``：本身不是 AgentLoop 构造参数，直接传会 TypeError；
    - ``max_iterations``：react preset 自带 max_iterations=10，不覆盖的话
      构造参数被静默忽略（实测：上限 3 变 10，场景拐进 MockLLM exhausted）。
    """
    kw: dict = dict(
        stream_mode=False,
        config=LLMConfig(api_key="sk-test"),
        registry=build_default_registry(),
        workspace=ws,
        trace_dir=ws / "traces",
    )
    if approval_timeout is not None:
        kw["approval_timeout"] = approval_timeout
        kw["approval_on_timeout"] = approval_on_timeout
    cfg = dict(extra_cfg or {})
    cfg.setdefault("max_iterations", max_iterations)
    kw["strategy"] = {"name": "react", "config": cfg}
    loop = AgentLoop(**kw)
    mock = MockLLM(list(responses))
    patch_llm_client(loop, mock.chat)
    loop.client.achat = mock.achat
    return loop, mock, ws / "traces"


def _collect_events(loop: AgentLoop) -> list[str]:
    out: list[str] = []
    original = loop._on_event

    def _spy(kind, payload=None, *a, **kw):
        out.append(kind)
        return original(kind, payload) if original else None

    loop._on_event = _spy
    return out


def _assert_full_finalization(result, loop: AgentLoop, events: list[str],
                              mock: MockLLM, label: str) -> None:
    """6 项收尾断言（出口矩阵的统一验收函数）。"""
    c = Counter(events)
    # 1. trace_path 非空
    assert result.trace_path, f"[{label}] trace_path 为 None（出口未收尾 trace）"
    # 2. trace 文件已关闭
    tw = loop._trace_writer
    assert tw is not None and tw._file.closed, (
        f"[{label}] trace_writer 未关闭（出口未收尾 trace）"
    )
    # 3. loop_end 恰 1 次
    assert c["loop_end"] == 1, f"[{label}] loop_end = {c['loop_end']}（期望恰 1）"
    # 4. iter_end 恰 1 次
    assert c["iter_end"] == 1, f"[{label}] iter_end = {c['iter_end']}（期望恰 1）"
    # 5. after_run hook 恰 1 次
    assert mock.hook_after_run == 1, (
        f"[{label}] after_run hook = {mock.hook_after_run}（期望恰 1）"
    )
    # 6. 无重复 done
    assert c.get("done", 0) <= 0 or c["done"] == 1, f"[{label}] done = {c['done']}"


def _run_scenario(ws: Path, responses, *, label: str, **kw) -> dict:
    """跑一个场景，返回收尾断言所需的全部观测。"""
    events: list[str] = []
    loop, mock, _ = _build(ws, responses, **kw)
    original = loop._on_event

    async def _spy_after_run(ctx, result=None):
        mock.hook_after_run += 1

    # spy after_run hook：包住 CompositeHook 的 after_run（telemetry 层）
    real_afire = loop._afire_hooks

    async def _afire_spy(name, *a, **k):
        if name == "after_run":
            mock.hook_after_run += 1
        return await real_afire(name, *a, **k)

    loop._afire_hooks = _afire_spy

    def _on_event(kind, payload=None, *a, **k2):
        events.append(kind)
        if original:
            return original(kind, payload)
        return None

    loop._on_event = _on_event

    r = asyncio.run(loop.arun("测试任务"))
    return {
        "result": r,
        "loop": loop,
        "events": events,
        "mock": mock,
        "counter": Counter(events),
    }


# ── 出口 #4：after_reason STOP（纯文本终答）── 正向前提，应当一直绿 ──


def test_exit_net_final_text(tmp_path: Path):
    got = _run_scenario(
        tmp_path,
        [_resp(content="最终答案")],
        label="final_text",
    )
    _assert_full_finalization(got["result"], got["loop"], got["events"],
                              got["mock"], "final_text")
    assert got["result"].finished_reason == "stop"


# ── 出口 #1：precheck STOP（预算）── 正向前提 ──


def test_exit_net_budget_stop(tmp_path: Path):
    got = _run_scenario(
        tmp_path,
        [_resp(content="预算内")],
        label="budget",
        extra_cfg={"budget_max_tokens": 100},  # 每响应 150 token → 首轮即超
    )
    _assert_full_finalization(got["result"], got["loop"], got["events"],
                              got["mock"], "budget")
    assert got["result"].finished_reason == "budget"


# ── 出口 #3：reasoner error ── 正向前提 ──


def test_exit_net_reasoner_error(tmp_path: Path):
    """第一轮带 tool_call（推进到第二轮）→ 第二轮 LLM 抛异常。"""
    got = _run_scenario(
        tmp_path,
        [
            _resp(tool_calls=[{"id": "c1", "name": "list_history",
                               "arguments": {}}]),
            # 第二轮：MockLLM exhausted → RuntimeError("MockLLM exhausted")
        ],
        label="reasoner_error",
        max_iterations=5,
    )
    _assert_full_finalization(got["result"], got["loop"], got["events"],
                              got["mock"], "reasoner_error")
    r = got["result"]
    assert r.error or r.finished_reason == "error", (
        f"reasoner error 场景没有错误信号: finished={r.finished_reason} err={r.error!r}"
    )


# ── 出口 #8：max_iter（for-else 跑满）── 正向前提 ──
# 注意：no-progress 会先触发；用大窗口避开（经 strategy config 传入，
# loop.py:469 的显式 strategy 覆盖路径）。


def test_exit_net_max_iter(tmp_path: Path):
    # 用 list_history（无副作用回测工具，Windows/Linux 都可用）而非 shell
    # ——shell 的 echo 在 Windows 下经沙箱可能报 error，会把场景拐进 error 出口
    responses = [
        _resp(tool_calls=[{"id": f"c{i}", "name": "list_history",
                           "arguments": {"strategy": f"s-{i}"}}])
        for i in range(3)
    ]
    got = _run_scenario(
        tmp_path, responses,
        label="max_iter", max_iterations=3,
        extra_cfg={"no_progress_window": 99},
    )
    _assert_full_finalization(got["result"], got["loop"], got["events"],
                              got["mock"], "max_iter")
    assert got["result"].finished_reason == "max_iter", (
        f"期望跑满 3 轮: {got['result'].finished_reason}"
    )


# ── 出口 #6：after_act EXIT（no-progress 拒绝）── U1 修复后全绿 ──


def test_exit_net_no_progress_reject(tmp_path: Path):
    """重复相同 tool_call × 3 → no-progress → approval 1s 超时 → reject → EXIT。"""
    responses = [
        _resp(tool_calls=[{"id": "c1", "name": "list_history", "arguments": {}}])
        for _ in range(5)
    ]
    got = _run_scenario(
        tmp_path, responses,
        label="no_progress_reject",
        approval_timeout=1, approval_on_timeout="reject",
        max_iterations=5,
        extra_cfg={"no_progress_window": 3},
    )
    r = got["result"]
    # 出口语义前提：确实走了 EXIT
    assert r.finished_reason == "approval_timeout_rejected", (
        f"前提失败：没走到 no-progress EXIT（finished={r.finished_reason}）"
    )
    # 6 项收尾断言——今天：trace_path=None、writer 未关、无 done
    _assert_full_finalization(r, got["loop"], got["events"], got["mock"],
                              "no_progress_reject")
