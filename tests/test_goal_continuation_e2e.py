"""批次 13 · S3-a 回归：真实 goal 续跑在两个引擎上都生效。

**为什么这个测试不能省**：``test_engine_parity.py`` 用 mock injector 断
「``inject_post_response`` 被调用」，但那不能证明**真实**的
``GoalContinuationInjector`` 在默认引擎上还能工作——它依赖
``resolve("goal.context")``、``_get_goal_store()``、真实 GoalStore 快照。
批次 13 实测：翻默认后这条链在 skeleton 上整条断开（1 轮就停）。

本测试用真实 GoalStore + 真实 ``build_default_injectors()``，只 mock LLM。
两个引擎都必须跑到 ``max_iter``（因为判据永远不被覆盖 = 永远需要续跑）。
"""
from __future__ import annotations
from llm_client_mock import patch_llm_client

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_INNER = r'''
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"{root}\src")

ws = Path(tempfile.mkdtemp())
db = ws / "goals.db"
os.environ["AGENTNODES_GOAL_DB_PATH"] = str(db)

import agentnodes.apps.capabilities  # noqa: F401  (注册 goal.* capabilities)
from agentnodes.kernel.agent.capability import resolve
from agentnodes.foundation.llm import LLMConfig, LLMResponse
from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.loop.core import AgentLoop


def _text(t):
    return LLMResponse(content=t, tool_calls=[], finish_reason="stop",
                       usage={"prompt_tokens": 10, "completion_tokens": 5,
                              "total_tokens": 15})


class MockLLM:
    def __init__(self, rs):
        self.rs = list(rs)

    def chat(self, m, **k):
        if not self.rs:
            raise RuntimeError("exhausted")
        return self.rs.pop(0)

    async def achat(self, m, **k):
        return self.chat(m)


class NullHook:
    async def before_run(self, *a): pass
    async def before_iteration(self, *a): pass
    async def after_iteration(self, *a): pass
    async def after_run(self, *a): pass
    async def on_error(self, *a): pass
    async def before_execute_tools(self, *a): pass
    async def on_tool_error(self, *a): pass
    async def after_tool_executed(self, *a): pass
    async def on_guardrail_finding(self, *a): pass


async def main():
    (ws / "strategies" / "foo").mkdir(parents=True)
    GoalStore = resolve("goal.store")
    store = GoalStore(db_path=db)
    sess = "ses_goal_continuation_e2e"
    store.replace_goal(
        session_id=sess,
        objective="长程研究：验证续跑",
        criteria=["找到至少 3 个候选", "对比后给出结论"],
    )
    snap = store.get_current_snapshot(sess)
    assert snap and snap.get("goal"), "goal 未创建"
    assert len(snap.get("criteria") or []) == 2, snap

    for engine in sys.argv[1:]:
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=ws,
            session_id=sess,
            max_iterations=3,
            approval_timeout=1,
            approval_on_timeout="reject",
            hooks=NullHook(),
        )
        mock = MockLLM([_text("第%d版" % i) for i in range(6)])
        loop.client.chat = mock.chat
        loop.client.achat = mock.achat
        r = await loop.arun("研究任务", engine=engine)
        print("@@%s@@" % engine + "%d|%s" % (r.iterations, r.finished_reason))
    store.close()


asyncio.run(asyncio.wait_for(main(), timeout=120))
'''.replace("{root}", str(ROOT))


def _run(*engines: str) -> dict[str, tuple[int, str]]:
    r = subprocess.run(
        [sys.executable, "-c", _INNER, *engines],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=240,
    )
    if r.returncode != 0:
        pytest.fail(f"subprocess failed:\n{r.stdout}\n{r.stderr}")
    out: dict[str, tuple[int, str]] = {}
    for line in r.stdout.splitlines():
        for e in engines:
            marker = f"@@{e}@@"
            if line.startswith(marker):
                iters, finished = line[len(marker):].split("|", 1)
                out[e] = (int(iters), finished)
    return out


def test_goal_continuation_runs_on_both_engines():
    """真实 goal 有未覆盖判据时，两个引擎都必须续跑到 max_iter。

    批次 13 修复前：legacy 3 轮 / skeleton **1 轮**（skeleton 丢掉了
    order>=100 的 injector，且 TextFinalDecider 排在 GoalContinuation
    之前使其成为死代码）。
    """
    res = _run("legacy", "skeleton")
    assert set(res) == {"legacy", "skeleton"}, f"引擎输出缺失: {res}"

    legacy_iters, legacy_finished = res["legacy"]
    skel_iters, skel_finished = res["skeleton"]

    # 前提：场景真的触发了续跑（否则测试是空集合）
    assert legacy_iters == 3, f"legacy 未续跑（场景失效）: {res}"
    assert legacy_finished == "max_iter", res

    # 核心断言：skeleton 行为一致
    assert skel_iters == legacy_iters, (
        f"skeleton 未按 goal 续跑: legacy={res['legacy']} skeleton={res['skeleton']}"
    )
    assert skel_finished == legacy_finished, res


def test_goal_continuation_final_answer_text_matches():
    """续跑到 max_iter 时两个引擎给出的终答文案一致（S3-a 缺口 5）。"""
    res = _run("legacy", "skeleton")
    assert res["legacy"][1] == res["skeleton"][1] == "max_iter", res
