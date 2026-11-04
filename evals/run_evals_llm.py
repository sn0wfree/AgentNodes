"""Run golden tasks against a real LLM via AgentLoop (A7).

**2026-11-04 迁移**：原实现 import 的是 ``strategy_research.core.*``，
那个包在四层骨架重构中已不存在（功能迁到 ``kernel`` / ``foundation``）。
本文件随之改到新位置——此前这个运行器是坏的，任何人跑都会
``ModuleNotFoundError``。

与 ``run_evals.py`` 的分工：那个用模拟 run 验证 scorecard 本身，
这个真跑 LLM 拿真实分数。RSI 的验证信号走这个。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from evals.scorecard import (  # noqa: E402
    GoldenTask,
    RunRecord,
    build_scorecard,
    load_golden_tasks,
)


def _run_one(task: GoldenTask, *, workspace: Path, max_iterations: int = 6) -> RunRecord:
    from strategy_research.foundation.llm.config import LLMConfig
    from strategy_research.kernel.agent.builtin_tools import build_default_registry
    from strategy_research.kernel.agent.loop import AgentLoop
    from strategy_research.kernel.agent.strategy.loop_strategy import LoopConfig
    from strategy_research.kernel.agent.toolsets import apply_toolset, resolve_toolset

    cfg = LLMConfig.load()
    registry = build_default_registry(workspace)
    # apply_toolset 直接接受 toolset 名/列表（旧实现多走了一步 resolve）
    if task.toolset:
        registry = apply_toolset(registry, resolve_toolset(task.toolset))

    loop_cfg = LoopConfig(
        max_iterations=max_iterations,
        budget_max_tokens=task.budget_max_tokens,
        budget_max_seconds=45.0,
    )

    loop = AgentLoop(
        config=cfg,
        registry=registry,
        workspace=workspace,
        max_iterations=max_iterations,
        system_prompt=(
            "你是量化研究助手。用工具完成任务后给出简洁结论。"
            "不要编造数据；必要时说明信息不足。"
        ),
        auto_git_commit=False,
        enable_goal_injection=False,
        enable_memory_injection=False,
        enable_hypothesis_auto_create=False,
        strategy=None,
    )
    # Drive A1 budget via strategy config if present.
    try:
        loop._strategy.config = loop_cfg
    except Exception:  # noqa: BLE001
        pass

    try:
        result = loop.run(task.prompt)
        tools_used = []
        evidence_parts = []
        for msg in result.messages or []:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    name = (tc.get("function") or {}).get("name") or tc.get("name")
                    if name:
                        tools_used.append(name)
            content = msg.get("content") or ""
            if content and isinstance(content, str):
                evidence_parts.append(content[:2000])
        evidence = "\n".join(evidence_parts)[:12000]
        return RunRecord(
            task_id=task.id,
            tools_used=tools_used,
            tokens_used=int(
                getattr(result, "metrics", {}).get("usage_tokens")
                or getattr(loop, "_budget_tokens", 0)
                or getattr(result, "metrics", {}).get("tokens")
                or 0
            ),
            finished_reason=getattr(result, "finished_reason", "stop"),
            answer=(result.answer or "")[:4000],
            evidence=evidence,
            success=bool(getattr(result, "success", True)),
            error=getattr(result, "error", None),
            iterations=int(getattr(result, "iterations", 0) or 0),
            tool_calls_made=int(getattr(result, "tool_calls_made", 0) or 0),
            tool_sequence=list(tools_used),
        )
    except Exception as exc:  # noqa: BLE001
        return RunRecord(
            task_id=task.id,
            tools_used=[],
            tokens_used=0,
            finished_reason="error",
            answer="",
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Real-LLM golden evals")
    parser.add_argument(
        "--tasks",
        default=str(Path(__file__).parent / "golden_tasks.jsonl"),
    )
    parser.add_argument("--workspace", default=str(ROOT / "evals" / "_ws"))
    parser.add_argument("--max-iterations", type=int, default=6)
    parser.add_argument("--only", default="", help="comma-separated task ids")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    tasks = load_golden_tasks(args.tasks)
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        tasks = [t for t in tasks if t.id in want]

    ws = Path(args.workspace)
    ws.mkdir(parents=True, exist_ok=True)

    runs: list[RunRecord] = []
    for task in tasks:
        print(f"→ {task.id} …", flush=True, file=sys.stderr)
        rec = _run_one(task, workspace=ws, max_iterations=args.max_iterations)
        runs.append(rec)
        tools = ",".join(rec.tools_used) or "-"
        print(
            f"  reason={rec.finished_reason} tokens={rec.tokens_used} "
            f"tools={tools} ok={rec.success}",
            flush=True, file=sys.stderr,
        )
        if rec.error:
            print(f"  error={rec.error}", flush=True, file=sys.stderr)

    card = build_scorecard(tasks, runs)
    payload = card.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"\nmean={payload['mean']} pass@0.7={payload['pass_rate@0.7']} n={payload['n']}")
        for row in payload["tasks"]:
            note = ("; " + ",".join(row["notes"])) if row["notes"] else ""
            print(f"  {row['id']}: {row['total']}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
