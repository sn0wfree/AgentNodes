"""R-A: branches[].action=retry now actually re-runs the target.

docs/plan-retry-fix-and-workflow-migration-20261031.md §1.
Before this fix the retry action appended the target back to a remaining
layer, where the split node's "already has a result" filter discarded it —
the agent never ran twice. The fix threads an explicit retry_queue through
the graph state.

Semantics (unchanged precondition): retry applies to an agent that ALREADY
ran — `_apply_branch_action` requires ``target in layer_results``. The
branch condition is typically evaluated on a later layer (e.g. a gate that
inspects an upstream worker's output).
"""
from __future__ import annotations

import json
from pathlib import Path

from strategy_research.kernel.swarm.runtime import (
    AgentResult,
    AgentStatus,
    SwarmPreset,
    SwarmRuntime,
)
from strategy_research.kernel.swarm.types import AgentCall


def _call(name: str) -> AgentCall:
    return AgentCall(
        agent_name=name, prompt="",
        context={"executor_type": "llm", "allow_stub": True},
    )


def _preset(dag, branches, **kw) -> SwarmPreset:
    return SwarmPreset(
        name="t", description="", agents=[_call(n) for n in dag],
        dag=dag, branches=branches, **kw,
    )


# worker 在第 0 层跑；gate 在第 1 层判定 worker 需要重跑。
DAG = {"worker": [], "gate": ["worker"], "tail": ["gate"]}
RETRY_COND = "worker.output.needs_retry == 1"


def _runtime(calls: dict[str, int], worker_outputs: list[str] | None = None):
    """Runtime with scripted per-agent payloads (stub path, no LLM)."""
    rt = SwarmRuntime()
    seq: dict[str, int] = {}

    def wrapped(agent_call, workspace, task, upstream):
        name = agent_call.agent_name
        calls[name] = calls.get(name, 0) + 1
        seq[name] = seq.get(name, 0) + 1
        if name == "worker":
            payload = {"needs_retry": 1}
            if worker_outputs is not None:
                payload["tag"] = worker_outputs[
                    min(seq[name] - 1, len(worker_outputs) - 1)
                ]
        else:
            payload = {}
        return AgentResult(
            agent_id=name, status=AgentStatus.SUCCESS,
            output=json.dumps(payload), summary="s",
        )

    rt._execute_agent = wrapped
    return rt


def test_retry_action_really_reruns_the_agent(tmp_path: Path):
    calls: dict[str, int] = {}
    rt = _runtime(calls)
    rt.execute(
        _preset(DAG, [{"condition": RETRY_COND, "action": "retry",
                       "target": "worker"}]),
        tmp_path, "task",
    )
    # worker：首跑 1 + 因 retry 重跑 1 = 2
    assert calls["worker"] == 2, calls


def test_retry_is_capped_by_branch_max_retries(tmp_path: Path):
    """条件恒真：重跑次数受 branch_max_retries 限制，不会无限循环。"""
    calls: dict[str, int] = {}
    rt = _runtime(calls)
    rt.execute(
        _preset(DAG, [{"condition": RETRY_COND, "action": "retry",
                       "target": "worker"}], branch_max_retries=2),
        tmp_path, "task",
    )
    assert calls["worker"] == 3, calls   # 1 首跑 + 2 次重跑


def test_retry_zero_disables_rerun(tmp_path: Path):
    calls: dict[str, int] = {}
    rt = _runtime(calls)
    rt.execute(
        _preset(DAG, [{"condition": RETRY_COND, "action": "retry",
                       "target": "worker"}], branch_max_retries=0),
        tmp_path, "task",
    )
    assert calls["worker"] == 1, calls


def test_retry_overwrites_previous_result(tmp_path: Path):
    """重跑产生的新结果覆盖旧条目。"""
    calls: dict[str, int] = {}
    rt = _runtime(calls, worker_outputs=["first", "second"])
    result = rt.execute(
        _preset(DAG, [{"condition": RETRY_COND, "action": "retry",
                       "target": "worker"}]),
        tmp_path, "task",
    )
    payload = json.loads(result.agent_results["worker"].output)
    assert payload["tag"] == "second"


def test_skip_still_works(tmp_path: Path):
    """skip 语义不受 R-A 影响。"""
    calls: dict[str, int] = {}
    rt = _runtime(calls)
    result = rt.execute(
        _preset({"worker": [], "gate": ["worker"], "tail": ["gate"]},
                [{"condition": RETRY_COND, "action": "skip", "target": "tail"}]),
        tmp_path, "task",
    )
    assert "tail" not in result.agent_results


def test_no_branches_means_single_run(tmp_path: Path):
    calls: dict[str, int] = {}
    rt = _runtime(calls)
    rt.execute(_preset(DAG, []), tmp_path, "task")
    assert calls["worker"] == 1
