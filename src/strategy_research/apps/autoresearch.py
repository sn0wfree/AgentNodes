"""Autoresearch 循环辅助函数。

Main Process = Orchestrator + Main Agent,串行 spawn 每个 Subagent via Task tool。
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# ── W-split re-exports ──────────────────────────────────────────────
from strategy_research.core.study.lazy_detect import detect_lazy_behavior  # noqa: E402,F401
from strategy_research.core.study.run_summary import (  # noqa: E402,F401
    generate_run_summary,
    load_run_summary,
    save_run_summary,
)
from strategy_research.core.study.stub_agents import _stub_agent_output  # noqa: E402,F401

logger = logging.getLogger(__name__)

# 默认配置: 保留最近 N 轮详细数据 (其他轮次优先读取 summary.json)
DEFAULT_KEEP_RECENT = 10


def build_agent_prompt(
    agent_name: str,
    prompts_dir: Path,
    current_state: dict[str, Any],
    previous_outputs: list[dict[str, Any]] | None = None,
) -> str:
    """构造 Agent prompt,用于 Task tool spawn。

    Args:
        agent_name: Agent 名称 (如 "researcher", "data_quality")
        prompts_dir: .prompts/ 目录路径
        current_state: 当前状态 (strategy_py, best_calmar, recent_runs)
        previous_outputs: 之前的 Agent 输出列表

    Returns:
        完整的 prompt 字符串
    """
    # 1. 角色定义 (从 .prompts/*.md 读取)
    prompt_file = prompts_dir / f"{agent_name}.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")
    role_def = prompt_file.read_text(encoding="utf-8")

    # 2. 当前状态
    state_info = f"""
## 当前状态

- strategy.py:
```python
{current_state.get('strategy_py', '')}
```

- 最佳 Calmar: {current_state.get('best_calmar', 0)}
- 当前 Calmar: {current_state.get('current_calmar', 0)}
- 总轮数: {current_state.get('total_runs', 0)}

### 最近 10 轮结果
```
{current_state.get('recent_runs', '')}
```
"""

    # 3. 上一个 Agent 的输出
    prev_output = ""
    if previous_outputs:
        last_output = previous_outputs[-1]
        prev_output = f"""
## 上一个 Agent 的输出

```json
{json.dumps(last_output, indent=2, ensure_ascii=False)}
```
"""

    # 4. 输出格式要求
    output_format = """
## 输出要求

**必须返回纯 JSON,不要包含任何其他文本、解释或 markdown 代码块标记。**

直接以 { 开头,以 } 结尾。

示例格式:
{"key": "value", "number": 123}
"""

    return role_def + state_info + prev_output + output_format


def save_agent_record(
    run_dir: Path,
    agent_name: str,
    step: int,
    input_data: dict[str, Any],
    output_data: dict[str, Any],
    duration_ms: int = 0,
) -> Path:
    """保存 Agent 记录到 runs/run_XXXX/agents/。

    Args:
        run_dir: run 目录路径 (如 runs/run_0013/)
        agent_name: Agent 名称
        step: 步骤号 (1-6)
        input_data: 输入数据
        output_data: 输出数据
        duration_ms: 执行耗时 (毫秒)

    Returns:
        保存的文件路径
    """
    agents_dir = run_dir / "agents"
    # parents=True: run_research_round 事前用 _create_run_dir 建好整棵树，
    # 但本函数是公开辅助（29 处调用方，含 CLI 与 study），传入只含末端
    # 目录的路径时不该崩。零风险加固。
    agents_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "agent": agent_name,
        "timestamp": datetime.now().isoformat(),
        "step": step,
        "input": input_data,
        "output": output_data,
        "duration_ms": duration_ms,
    }

    filepath = agents_dir / f"{agent_name}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)

    return filepath


def read_current_state(
    workspace_path: Path,
    strategy_name: str,
    strategy_file: Path | None = None,
    results_tsv: Path | None = None,
) -> dict[str, Any]:
    """读取当前状态 (strategy.py + results.tsv)。

    Args:
        workspace_path: 工作区路径
        strategy_name: 策略名称
        strategy_file: strategy.py 位置。默认
            ``workspace/strategies/<name>/strategy.py``；study 场景传当轮
            run 目录内的策略快照。
        results_tsv: results.tsv 位置。默认
            ``workspace/strategies/<name>/runs/results.tsv``；study 场景传
            ``study/<id>/results.tsv``。

    Returns:
        当前状态字典
    """
    strategy_dir = workspace_path / "strategies" / strategy_name
    if strategy_file is None:
        strategy_file = strategy_dir / "strategy.py"
    if results_tsv is None:
        results_tsv = strategy_dir / "runs" / "results.tsv"

    # 读取 strategy.py
    strategy_py = strategy_file.read_text(encoding="utf-8") if strategy_file.exists() else ""

    # 读取 results.tsv
    lines = []
    if results_tsv.exists():
        content = results_tsv.read_text(encoding="utf-8").strip()
        if content:
            lines = content.split("\n")

    lines[0] if lines else ""
    recent_runs = "\n".join(lines[-10:]) if len(lines) > 1 else ""

    # 解析最佳 Calmar
    best_calmar = 0.0
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) >= 4:
            try:
                calmar = float(parts[3])
                best_calmar = max(best_calmar, calmar)
            except ValueError:
                pass

    return {
        "strategy_py": strategy_py,
        "best_calmar": best_calmar,
        "current_calmar": best_calmar,
        "total_runs": max(len(lines) - 1, 0),
        "recent_runs": recent_runs,
    }


def parse_agent_output(raw_output: str) -> dict[str, Any]:
    """解析 Agent 输出,自动处理 markdown 包裹。

    Args:
        raw_output: Agent 原始输出字符串

    Returns:
        解析后的字典,如果解析失败返回 {"error": "parse_failed", "raw": raw_output}
    """
    if not raw_output or not raw_output.strip():
        return {"error": "empty_output"}

    # 1. 尝试直接 JSON 解析
    try:
        return json.loads(raw_output)
    except json.JSONDecodeError:
        pass

    # 2. 尝试提取 ```json ... ``` 中的内容
    json_match = re.search(r"```json\s*\n?(.*?)\n?\s*```", raw_output, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # 3. 尝试提取 ``` ... ``` 中的内容 (可能是其他格式)
    code_match = re.search(r"```\s*\n?(.*?)\n?\s*```", raw_output, re.DOTALL)
    if code_match:
        try:
            return json.loads(code_match.group(1))
        except json.JSONDecodeError:
            pass

    # 4. 尝试提取 { ... } 或 [ ... ] 中的内容
    brace_match = re.search(r"(\{.*\})", raw_output, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(1))
        except json.JSONDecodeError:
            pass

    bracket_match = re.search(r"(\[.*\])", raw_output, re.DOTALL)
    if bracket_match:
        try:
            return json.loads(bracket_match.group(1))
        except json.JSONDecodeError:
            pass

    # 5. 所有尝试都失败
    return {"error": "parse_failed", "raw": raw_output[:1000]}


def retry_agent_spawn(
    spawn_fn,
    agent_name: str,
    max_retries: int = 3,
    retry_delay: float = 5.0,
) -> dict[str, Any]:
    """重试 Agent spawn,直到成功或达到最大重试次数。

    Uses exponential backoff via ``RetryPolicy`` when ``retry_delay`` is
    the base delay (default 5s). Sequence for max_retries=3:
        attempt 1→2: 5s  (× 0.5..1.5 jitter)
        attempt 2→3: 10s
    Capped at ``RetryPolicy.max_delay`` (60s).

    Args:
        spawn_fn: spawn 函数,返回原始字符串
        agent_name: Agent 名称 (用于日志)
        max_retries: 最大重试次数
        retry_delay: base delay in seconds (doubles each attempt, ±50% jitter)

    Returns:
        解析后的字典
    """
    from strategy_research.core.agent.circuit_breaker import RetryPolicy

    policy = RetryPolicy(
        max_retries=max_retries,
        base_delay=retry_delay,
        max_delay=60.0,
        jitter=True,
    )
    last_raw = ""
    for attempt in range(1, policy.max_retries + 1):
        try:
            raw_output = spawn_fn()
            last_raw = raw_output
            parsed = parse_agent_output(raw_output)

            # 检查是否解析成功
            if "error" not in parsed:
                return parsed

            # 解析失败 — 返回错误信息而非全量重启
            logger.warning(f"[autoresearch] {agent_name} 解析失败 (attempt {attempt}/{policy.max_retries}): {parsed.get('error')}")
            if attempt == policy.max_retries:
                return {
                    "error": "parse_failed",
                    "agent": agent_name,
                    "raw_output": last_raw[:500],
                    "hint": "Output must be valid JSON matching the required schema. "
                            "Fix the output format and try again.",
                }

        except Exception as e:
            logger.warning(f"[autoresearch] {agent_name} 执行异常 (attempt {attempt}/{policy.max_retries}): {e}")
            if attempt == policy.max_retries:
                return {"error": "execution_failed", "agent": agent_name, "detail": str(e)}

        # Exponential backoff before the next attempt
        if attempt < policy.max_retries:
            delay = policy.get_delay(attempt)
            time.sleep(delay)

    # 所有重试都失败
    return {"error": "max_retries_exceeded", "agent": agent_name, "attempts": policy.max_retries}


def get_cooldown_seconds(base_cooldown: float = 30.0, jitter: float = 10.0, min_cooldown: float = 1.0) -> float:
    """计算带随机抖动的 cooldown 时间。

    Args:
        base_cooldown: 基础 cooldown (秒)
        jitter: 随机抖动范围 (±秒)
        min_cooldown: 最小 cooldown (秒)

    Returns:
        实际 cooldown 时间 (秒)
    """
    actual = base_cooldown + random.uniform(-jitter, jitter)
    return max(min_cooldown, actual)


# ============================================================
# Lazy Detection (懒惰检测)
# ============================================================

def should_run_lazy_detection(round_num: int, interval: int = 10) -> bool:
    """判断是否应该运行懒惰检测。

    Args:
        round_num: 当前轮数
        interval: 检测间隔 (默认 10 轮)

    Returns:
        是否应该运行检测
    """
    return round_num > 0 and round_num % interval == 0


def read_agent_history(
    runs_dir: Path,
    agent_name: str,
    threshold: int = 10,
    current_round: int | None = None,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> list[dict[str, Any]]:
    """读取最近 N 轮的 Agent 记录 (按需加载摘要)。

    最近 keep_recent 轮: 读取详细 agents/*.json
    超过 keep_recent 轮: 读取 summary.json 中的 agent_summaries.{name}

    注意: 物理文件全部保留, 只是读取策略不同。

    Args:
        runs_dir: runs 目录路径
        agent_name: Agent 名称
        threshold: 读取最近 N 轮 (默认 10)
        current_round: 当前轮数 (用于判断是否读取详细)
        keep_recent: 保留详细数据的最近轮数 (默认 10)

    Returns:
        历史记录列表 [{"round": N, "output": {...}, "source": "detailed|summary"}, ...]
    """
    history = []

    # 获取所有 run 目录 (排序)
    run_dirs = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir() and d.name.startswith("run_")],
        key=lambda d: d.name
    )

    # 只读取最近 threshold 轮
    for run_dir in run_dirs[-threshold:]:
        run_name = run_dir.name
        try:
            round_num = int(run_name.split("_")[1]) if "_" in run_name else 0
        except (ValueError, IndexError):
            continue

        # 决定读取源
        read_detailed = (
            current_round is None
            or should_read_detailed(round_num, current_round, keep_recent)
        )

        if read_detailed:
            # 读取详细 agents/*.json
            agent_file = run_dir / "agents" / f"{agent_name}.json"
            if agent_file.exists():
                try:
                    with open(agent_file, "r", encoding="utf-8") as f:
                        record = json.load(f)
                    history.append({
                        "round": round_num,
                        "output": record.get("output", {}),
                        "timestamp": record.get("timestamp", ""),
                        "source": "detailed",
                    })
                    continue
                except (json.JSONDecodeError, KeyError, OSError):
                    pass

        # 读取 summary.json (摘要)
        summary = load_run_summary(run_dir)
        if summary and "agent_summaries" in summary:
            agent_summary = summary["agent_summaries"].get(agent_name, "")
            history.append({
                "round": round_num,
                "output": {
                    "summary": agent_summary,
                    "verdict": summary.get("verdict"),
                    "metrics": summary.get("metrics", {}),
                },
                "timestamp": summary.get("timestamp", ""),
                "source": "summary",
            })

    return history


def save_laziness_report(
    run_dir: Path,
    round_num: int,
    lazy_results: list[dict[str, Any]],
    overall_lazy_score: float,
) -> Path:
    """保存 laziness report 到 runs/run_XXXX/。

    Args:
        run_dir: run 目录路径
        round_num: 当前轮数
        lazy_results: 检测结果列表
        overall_lazy_score: 整体懒惰分数

    Returns:
        保存的文件路径
    """
    # 生成 summary
    lazy_agents = [r for r in lazy_results if r.get("issues")]
    if lazy_agents:
        agent_names = [r["agent"] for r in lazy_agents]
        summary = f"{'、'.join(agent_names)} 存在懒惰行为"
    else:
        summary = "所有 Agent 行为正常"

    report = {
        "round": round_num,
        "timestamp": datetime.now().isoformat(),
        "overall_lazy_score": overall_lazy_score,
        "agents": lazy_results,
        "summary": summary,
    }

    filepath = run_dir / "laziness_report.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    return filepath


# ============================================================
# Summary Generation (Phase 1)
# ============================================================

def should_read_detailed(
    round_num: int,
    current_round: int,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> bool:
    """判断 run 是否读取详细数据 (按距当前轮的距离)。

    Args:
        round_num: run 轮数
        current_round: 当前轮数
        keep_recent: 保留详细数据的最近轮数 (默认 10)

    Returns:
        True = 读取详细 agents/*.json
        False = 读取 summary.json
    """
    return current_round - round_num < keep_recent


def get_run_data(
    run_dir: Path,
    current_round: int,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> dict[str, Any]:
    """根据距当前轮数决定读取详细还是摘要。

    注意: 不删除任何文件, 只是选择读取源。物理文件全部保留。

    Args:
        run_dir: run 目录路径
        current_round: 当前轮数
        keep_recent: 保留详细数据的轮数

    Returns:
        {
            "detailed": bool,    # 是否为详细数据
            "source": str,       # "agents" | "summary" | "tsv" | "none"
            "data": dict,        # 完整 agents 或 summary 或 tsv 行
            "round": int,        # 轮数
        }
    """
    try:
        round_num = int(run_dir.name.split("_")[1])
    except (ValueError, IndexError):
        return {"detailed": False, "source": "none", "data": {}, "round": 0}

    if should_read_detailed(round_num, current_round, keep_recent):
        # 最近 keep_recent 轮: 读取详细
        agents_dir = run_dir / "agents"
        if agents_dir.exists():
            return {
                "detailed": True,
                "source": "agents",
                "data": _load_all_agents(agents_dir),
                "round": round_num,
            }

    # 超过 keep_recent 轮: 读取 summary
    summary = load_run_summary(run_dir)
    if summary:
        return {
            "detailed": False,
            "source": "summary",
            "data": summary,
            "round": round_num,
        }

    # 无 summary: 降级到 None (保留物理 agents/ 但不读取)
    return {
        "detailed": False,
        "source": "none",
        "data": {},
        "round": round_num,
    }


def _load_all_agents(agents_dir: Path) -> dict[str, dict[str, Any]]:
    """加载所有 agent 记录。

    Args:
        agents_dir: agents/ 目录路径

    Returns:
        {"agent_name": output_dict}
    """
    result = {}
    for agent_file in agents_dir.glob("*.json"):
        try:
            data = json.loads(agent_file.read_text(encoding="utf-8"))
            result[agent_file.stem] = data.get("output", {})
        except (json.JSONDecodeError, OSError):
            pass
    return result


# ── study/single-round extraction (2026-08-04) ──────────────────────


def spawn_agent(
    agent_name: str,
    workspace_path: Path,
    strategy_name: str,
    current_state: dict,
    previous_outputs: list,
    *,
    behavior: str | None = None,
    max_iterations: int = 8,
    strategy_dir: Path | None = None,
    runs_dir: Path | None = None,
    results_tsv: Path | None = None,
    write_roots: tuple[str, ...] | None = None,
    read_roots: tuple[str, ...] | None = None,
    loop_strategy: Any | None = None,
    on_event: Any | None = None,
) -> str:
    """Spawn a single agent (real LLM or stub) and return JSON text.

    Extracted from ``cli/commands/autoresearch.py::_spawn_agent`` so that
    the study executor can drive the same spawn logic. Behavior is
    identical to the CLI helper:

    - When ``should_use_real_llm()`` and no ``behavior`` override →
      call ``run_agent_via_llm`` (AgentLoop with the role prompt).
    - Otherwise → stub whose output pattern depends on ``behavior``
      ('static' / 'varying' / 'improving'), driven by the
      ``AUTORESEARCH_BEHAVIOR`` env var when no explicit override given.

    ``on_event`` 透传给 AgentLoop（agent 事件出口）。None = 事件丢弃。
    """
    from strategy_research.core.agent.role_factory import (
        run_agent_via_llm,
        should_use_real_llm,
    )

    use_real = behavior is None and should_use_real_llm()
    if use_real:
        try:
            task_lines = [f"你是 {agent_name}. 你的工作目录: {workspace_path}"]
            if current_state:
                task_lines.append("当前状态:")
                task_lines.append(
                    json.dumps(current_state, ensure_ascii=False, default=str)
                )
            return run_agent_via_llm(
                role=agent_name,
                workspace_path=workspace_path,
                strategy_name=strategy_name,
                task="\n".join(task_lines),
                previous_outputs=previous_outputs,
                max_iterations=max_iterations,
                strategy_dir=strategy_dir,
                runs_dir=runs_dir,
                results_tsv=results_tsv,
                write_roots=write_roots,
                read_roots=read_roots,
                loop_strategy=loop_strategy,
                on_event=on_event,
            )
        except Exception as exc:
            # 真 LLM 失败 → 退到 stub, 不让主循环崩
            logger.warning(f"⚠️  AgentLoop.run() 失败 ({agent_name}): {exc}; 退到 stub")  # noqa: T201
            # fall through to stub

    effective_behavior = behavior or os.environ.get("AUTORESEARCH_BEHAVIOR", "static")
    return _stub_agent_output(
        agent_name, current_state, previous_outputs, effective_behavior
    )


def _study_append_evidence(
    session_id: str,
    run_name: str,
    metrics: dict,
    strategist_output: dict,
) -> None:
    """Append research evidence for a study session's active goal.

    Mirrors ``cli/commands/autoresearch.py::_append_backtest_evidence``
    but takes an explicit ``session_id`` (the CLI helper hard-codes
    ``autoresearch-{strategy_name}``).
    """
    try:
        from strategy_research.core.goal import (
            EvidenceInput,
            GoalStore,
        )
        store = GoalStore()
        goal = store.get_current_goal(session_id)
        if goal is None:
            return
        text = (
            f"Backtest {run_name}: "
            f"Calmar={metrics.get('calmar', 'N/A')} "
            f"Sharpe={metrics.get('sharpe', 'N/A')} "
            f"MaxDD={metrics.get('max_dd', 'N/A')}"
        )
        criteria = store.list_criteria(goal.goal_id)
        criterion_id = criteria[0].criterion_id if criteria else None
        store.append_evidence(
            session_id=session_id,
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            evidence=EvidenceInput(
                text=text,
                criterion_id=criterion_id,
                evidence_type="backtest",
                run_id=run_name,
                source_provider="study",
                source_type="backtest_run",
                artifact_path=strategist_output.get("hypothesis", "")[:200],
            ),
        )
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).debug(
            "study append_evidence skipped: %s", exc,
        )


def _study_register_hypothesis(
    session_id: str,
    researcher_output: dict,
    run_name: str,
) -> None:
    """Register the researcher's hypothesis and link it to the study goal."""
    try:
        from strategy_research.core.goal import GoalStore
        from strategy_research.core.hypothesis import HypothesisRegistry

        thesis = researcher_output.get("hypothesis", "")
        if not thesis:
            return

        registry = HypothesisRegistry()
        title = f"{run_name}: {thesis[:60]}"
        existing = None
        for h in registry.list():
            if h.title == title:
                existing = h
                break
        if existing is None:
            hyp = registry.create(
                title=title,
                thesis=thesis[:200],
                status="exploring",
            )
        else:
            hyp = existing

        goal_store = GoalStore()
        goal = goal_store.get_current_goal(session_id)
        if goal is not None and hyp.goal_id != goal.goal_id:
            registry.link_goal(hyp.hypothesis_id, goal.goal_id)
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).debug(
            "study register_hypothesis skipped: %s", exc,
        )


# ── study single-round: pure extraction of cmd_autoresearch's loop body ──
#
# This is the *headless* version of one autoresearch round: it does the
# same Step1→Step6 work (read state → spawn 9 agents → backtest →
# evaluation → decide → summary) but returns a structured ``RoundResult``
# dict and does NOT print, sleep between agents, check stop conditions,
# or apply inter-round cooldown. Those concerns live with the caller
# (the study executor or the CLI loop). Reuses ``spawn_agent`` /
# ``run_backtest_script`` / ``decide`` / the study evidence helpers so
# there is a single source of truth for each step.
#
# Differences from the CLI loop body (intentional):
#   - Every agent step uses ``get_cooldown_seconds`` to compute a delay
#     and the caller decides whether to honour it. ``run_research_round``
#     accepts ``inter_agent_sleep`` (default 0.0) and sleeps that fixed
#     number of seconds between agents; this keeps the round self-contained
#     without hard-coding cooldown jitter that the scheduler already owns.
#   - ``session_id`` (study session) and ``acceptance_config`` (thresholds
#     override from metric_targets) are explicit parameters.
#   - ``behavior`` overrides the stub mode when an LLM key is absent.


def _run_lazy_detection(
    round_num: int,
    lazy_detection_interval: int,
    keep_recent: int,
    runs_dir: Path,
    run_dir: Path,
) -> None:
    """Run laziness detection for the round (pure sink, no steering)."""

    if not should_run_lazy_detection(round_num, lazy_detection_interval):
        return
    lazy_results = []
    for agent_name in (
        "researcher", "factor_analyst", "strategist", "anti_overfit_analyst",
    ):
        history = read_agent_history(
            runs_dir, agent_name, threshold=10,
            current_round=round_num, keep_recent=keep_recent,
        )
        if history:
            lazy_result = detect_lazy_behavior(
                agent_name, history[-1].get("output", {}), history
            )
            lazy_results.append({"agent": agent_name, **lazy_result})
    if lazy_results:
        overall = sum(r.get("lazy_score", 0) for r in lazy_results) / len(lazy_results)
        save_laziness_report(run_dir, round_num, lazy_results, overall)


def _patch_results_verdict(runs_dir: Path, run_name: str, verdict: str) -> None:
    """Flip the pending row for ``run_name`` in results.tsv to the verdict."""
    results_path = runs_dir / "results.tsv"
    if not results_path.exists():
        return
    content = results_path.read_text(encoding="utf-8")
    lines = content.strip().split("\n")
    for i in range(len(lines) - 1, 0, -1):
        if lines[i].startswith(run_name + "\t") or lines[i].startswith(run_name + " "):
            parts = lines[i].split("\t")
            if len(parts) >= 12:
                parts[11] = verdict
                lines[i] = "\t".join(parts)
            break
    results_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_research_round(
    workspace_path: Path,
    strategy_name: str,
    round_num: int,
    *,
    session_id: str | None = None,
    acceptance_config: Any = None,
    max_retries: int = 3,
    lazy_detection_interval: int = 10,
    keep_recent: int = 10,
    behavior: str | None = None,
    inter_agent_sleep: float = 0.0,
    previous_summary: dict | None = None,
    directives: str | None = None,
    on_event: Any | None = None,
) -> dict:
    """Execute one autoresearch round and return a structured result.

    Args:
        workspace_path: workspace root containing ``strategies/{name}/``.
        strategy_name: the strategy directory under ``strategies/``.
        round_num: 1-based round index for this call.
        session_id: study/chat session id used to append goal evidence
            and link the researcher hypothesis. When ``None``, no goal
            evidence is appended (matches the legacy CLI semantics
            before P3-D3 hooked the active-session goal).
        acceptance_config: ``AcceptanceConfig`` override (e.g. from a
            study's ``metric_targets``). When ``None`` the default
            ``load_config`` is used. Passing a config avoids re-reading
            the workspace ``acceptance.yaml`` each round when the study
            already decided thresholds at creation time.
        max_retries: agent spawn retry budget (passed to
            ``retry_agent_spawn``).
        lazy_detection_interval / keep_recent: lazy-detection tuning.
            Detection only runs when
            ``round_num % lazy_detection_interval == 0``.
        behavior: stub mode override ('static'/'varying'/'improving').
            ``None`` defers to ``should_use_real_llm()``.
        inter_agent_sleep: seconds to ``time.sleep`` between agent
            spawns (default 0.0 — the scheduler owns cooldown policy).
        previous_summary: previous round's summary dict; passed to
            ``generate_run_summary`` so it can compute
            ``performance_change``. ``None`` on first round.

    Returns:
        ``RoundResult`` dict::

            {
              "round": round_num,
              "run_name": "run_0007",
              "run_dir": <Path>,
              "metrics": {...},          # may be {} on backtest failure
              "verdict": "keep"|"discard",
              "decision": decision.to_dict(),
              "agent_outputs": {...},    # 9 agents → output dict
              "summary": {...},          # generate_run_summary result
              "backtest_error": str|None,
            }
    """
    # Local imports keep the module import graph light for callers that
    # only need the helpers above.
    from strategy_research.core.backtest import run_backtest_script
    from strategy_research.apps.strategy_acceptance import (
        decide as make_decision,
    )
    from strategy_research.apps.strategy_acceptance import (
        load_config as load_acceptance_config,
    )

    path = Path(workspace_path).resolve()
    strategy_dir = path / "strategies" / strategy_name

    # ── Step 1: read state ──────────────────────────────────────────
    current_state = read_current_state(path, strategy_name)

    # ── run directory: round_num → run_NNNN (matches backtest naming) ──
    runs_dir = strategy_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    existing_nums: list[int] = []
    if runs_dir.exists():
        for d in runs_dir.iterdir():
            if d.is_dir() and d.name.startswith("run_"):
                try:
                    existing_nums.append(int(d.name.split("_")[1]))
                except (ValueError, IndexError):
                    pass
    run_num_int = max(existing_nums, default=0) + 1
    run_name = f"run_{run_num_int:04d}"
    run_dir = runs_dir / run_name
    run_dir.mkdir(exist_ok=True)
    (run_dir / "agents").mkdir(exist_ok=True)

    # ── Lazy detection (every Nth round) ────────────────────────────
    # Pure sink: the report is written for inspection but does not steer
    # the round itself.
    _run_lazy_detection(
        round_num, lazy_detection_interval, keep_recent, runs_dir, run_dir
    )

    def _spawn(name: str, prevs: list) -> dict:
        # retry_agent_spawn parses raw output internally and returns a dict.
        agent_on_event = on_event
        if agent_on_event is not None:
            def agent_on_event(event_type: str, data: dict, _n=name, _f=on_event):
                _f(event_type, {**data, "agent": _n})
        out = retry_agent_spawn(
            lambda: spawn_agent(
                name, path, strategy_name, current_state, prevs,
                behavior=behavior,
                on_event=agent_on_event,
            ),
            name,
            max_retries=max_retries,
        )
        if inter_agent_sleep:
            time.sleep(inter_agent_sleep)
        return out

    # ── Step 2: researcher ─────────────────────────────────────────
    # Phase 2: if mid-execution directives were issued by the user, the
    # executor passes them through; inject into current_state so the
    # researcher agent sees them in its prompt.
    if directives:
        current_state = {**current_state, "user_directives": directives}
    researcher_output = _spawn("researcher", [])
    save_agent_record(
        run_dir, "researcher", 2, current_state, researcher_output,
    )
    if session_id:
        _study_register_hypothesis(session_id, researcher_output, run_name)

    # ── Step 3: execution agents (DQ → factor → strategist → portfolio) ─
    data_quality_output = _spawn(
        "data_quality", [researcher_output]
    )
    save_agent_record(
        run_dir, "data_quality", 3,
        {"researcher": researcher_output}, data_quality_output,
    )

    factor_analyst_output = _spawn(
        "factor_analyst",
        [researcher_output, data_quality_output],
    )
    save_agent_record(
        run_dir, "factor_analyst", 3,
        {"researcher": researcher_output, "data_quality": data_quality_output},
        factor_analyst_output,
    )

    strategist_output = _spawn(
        "strategist",
        [researcher_output, data_quality_output, factor_analyst_output],
    )
    save_agent_record(
        run_dir, "strategist", 3,
        {"researcher": researcher_output, "factor_analyst": factor_analyst_output},
        strategist_output,
    )

    portfolio_construction_output = _spawn("portfolio_construction", [strategist_output])
    save_agent_record(
        run_dir, "portfolio_construction", 3,
        {"strategist": strategist_output}, portfolio_construction_output,
    )

    # ── Step 4: backtest ───────────────────────────────────────────
    backtest_result = run_backtest_script(
        workspace_path=path,
        strategy_name=strategy_name,
        action=strategist_output.get("action", "unknown"),
        description=strategist_output.get("hypothesis", ""),
        run_dir=run_dir,
    )
    backtest_error: str | None = None
    if backtest_result.get("success"):
        metrics = backtest_result.get("metrics", {})
        if session_id:
            _study_append_evidence(
                session_id, run_name, metrics, strategist_output,
            )
    else:
        backtest_error = backtest_result.get("error", "unknown")
        metrics = {}

    # ── Step 5: evaluation agents (risk → attribution → anti-overfit → diag) ─
    risk_controller_output = _spawn("risk_controller", [metrics])
    save_agent_record(
        run_dir, "risk_controller", 5, {"metrics": metrics},
        risk_controller_output,
    )

    attribution_analyst_output = _spawn(
        "attribution_analyst", [metrics, risk_controller_output]
    )
    save_agent_record(
        run_dir, "attribution_analyst", 5,
        {"metrics": metrics, "risk_controller": risk_controller_output},
        attribution_analyst_output,
    )

    anti_overfit_analyst_output = _spawn(
        "anti_overfit_analyst",
        [metrics, risk_controller_output, attribution_analyst_output],
    )
    save_agent_record(
        run_dir, "anti_overfit_analyst", 5,
        {"metrics": metrics, "risk_controller": risk_controller_output,
         "attribution_analyst": attribution_analyst_output},
        anti_overfit_analyst_output,
    )

    backtest_diagnostics_output = _spawn(
        "backtest_diagnostics",
        [backtest_result.get("run_log", ""), metrics],
    )
    save_agent_record(
        run_dir, "backtest_diagnostics", 5,
        {"run_log": backtest_result.get("run_log", ""), "metrics": metrics},
        backtest_diagnostics_output,
    )

    # ── Step 6: decide ─────────────────────────────────────────────
    aoa_llm_verdict = None
    if isinstance(anti_overfit_analyst_output, dict):
        aoa_llm_verdict = {
            "passed": bool(anti_overfit_analyst_output.get("overfit_passed", False)),
            "score": float(anti_overfit_analyst_output.get("overfit_score", 0.5) or 0.5),
            "reason": anti_overfit_analyst_output.get("verdict_reason", ""),
            "concerns": anti_overfit_analyst_output.get("methods_passed", []),
            "source": "anti_overfit_analyst",
        }

    if acceptance_config is None:
        acceptance_config = load_acceptance_config(
            workspace_config=path / "acceptance.yaml",
        )
    decision = make_decision(
        metrics=metrics,
        llm_verdict=aoa_llm_verdict,
        cfg=acceptance_config,
        stagnation_count=int(
            anti_overfit_analyst_output.get("stagnation_count", 0) or 0
        ) if isinstance(anti_overfit_analyst_output, dict) else 0,
    )
    verdict = "keep" if decision.accept else "discard"

    # Update results.tsv status (the backtest wrote a 'pending' row; flip
    # it to the final keep/discard verdict on this run's line, matching
    # the CLI's Step 6 in-place patch).
    _patch_results_verdict(runs_dir, run_name, verdict)

    agent_outputs = {
        "researcher": researcher_output,
        "data_quality": data_quality_output,
        "factor_analyst": factor_analyst_output,
        "strategist": strategist_output,
        "portfolio_construction": portfolio_construction_output,
        "risk_controller": risk_controller_output,
        "attribution_analyst": attribution_analyst_output,
        "anti_overfit_analyst": anti_overfit_analyst_output,
        "backtest_diagnostics": backtest_diagnostics_output,
    }

    summary = generate_run_summary(
        agent_outputs, metrics, verdict, round_num, previous_summary,
    )
    summary["acceptance_decision"] = decision.to_dict()

    # 注入因子失败信息供下一轮参考
    factor_failures = backtest_result.get("factor_failures", [])
    if factor_failures:
        summary["factor_failures"] = factor_failures

    save_run_summary(run_dir, summary)

    return {
        "round": round_num,
        "run_name": run_name,
        "run_dir": run_dir,
        "metrics": metrics,
        "verdict": verdict,
        "decision": decision.to_dict(),
        "agent_outputs": agent_outputs,
        "summary": summary,
        "backtest_error": backtest_error,
    }


# ============================================================
# Phase Functions — split from run_research_round for AEGIS
# ============================================================
#
# These three functions split the monolithic run_research_round into
# independent phases so the AutoresearchRunner can inject AEGIS logic
# (novelty gate, journal, attribution) between phases.
#
# Phase 1: researcher + lazy detection + hypothesis registration
# Phase 2: data_quality → factor_analyst → strategist → portfolio → backtest
# Phase 3: risk_controller → attribution_analyst → anti_overfit → decide
#
# run_research_round is kept as-is for backward compat; it is NOT
# refactored to call these (yet).  The runner uses them directly.


def _create_run_dir(
    workspace_path: Path,
    strategy_name: str,
    runs_dir: Path | None = None,
) -> tuple[Path, str, Path]:
    """Create the run directory for a round. Returns (runs_dir, run_name, run_dir).

    v2: ``runs_dir`` overrides the default ``workspace/strategies/<name>/runs``
    (study scenario: ``study/<id>/rounds/round_NNNN`` — run numbering is
    per-round; recovered rounds continue at max+1).
    """
    if runs_dir is None:
        strategy_dir = workspace_path / "strategies" / strategy_name
        runs_dir = strategy_dir / "runs"
    else:
        runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    existing_nums: list[int] = []
    if runs_dir.exists():
        for d in runs_dir.iterdir():
            if d.is_dir() and d.name.startswith("run_"):
                try:
                    existing_nums.append(int(d.name.split("_")[1]))
                except (ValueError, IndexError):
                    pass
    run_num_int = max(existing_nums, default=0) + 1
    run_name = f"run_{run_num_int:04d}"
    run_dir = runs_dir / run_name
    run_dir.mkdir(exist_ok=True)
    (run_dir / "agents").mkdir(exist_ok=True)
    return runs_dir, run_name, run_dir


def _make_spawn_fn(
    path: Path,
    strategy_name: str,
    current_state: dict,
    behavior: str | None = None,
    max_retries: int = 3,
    max_iterations: int = 8,
    inter_agent_sleep: float = 0.0,
    strategy_dir: Path | None = None,
    runs_dir: Path | None = None,
    results_tsv: Path | None = None,
    write_roots: tuple[str, ...] | None = None,
    read_roots: tuple[str, ...] | None = None,
    loop_strategy: Any | None = None,
    on_event: Any | None = None,
):
    """Create the _spawn closure for agent execution.

    ``on_event``: agent 事件出口 ``(event_type, data) -> None``。这里是在
    转发链上**唯一**知道「当前 spawn 的是哪个 agent」的位置，所以 agent
    名在这里注入（``data["agent"] = name``），下游 sink 只管过滤与落库。
    None = 现行为（事件丢弃）。
    """
    def _spawn(name: str, prevs: list) -> dict:
        agent_on_event = on_event
        if agent_on_event is not None:
            def agent_on_event(event_type: str, data: dict, _n=name, _f=on_event):
                _f(event_type, {**data, "agent": _n})
        out = retry_agent_spawn(
            lambda: spawn_agent(
                name, path, strategy_name, current_state, prevs,
                behavior=behavior,
                strategy_dir=strategy_dir,
                runs_dir=runs_dir,
                results_tsv=results_tsv,
                write_roots=write_roots,
                read_roots=read_roots,
                max_iterations=max_iterations,
                loop_strategy=loop_strategy,
                on_event=agent_on_event,
            ),
            name,
            max_retries=max_retries,
        )
        if inter_agent_sleep:
            time.sleep(inter_agent_sleep)
        return out
    return _spawn


def run_researcher_phase(
    workspace_path: Path | str,
    strategy_name: str,
    current_state: dict,
    run_dir: Path,
    *,
    session_id: str | None = None,
    run_name: str = "",
    behavior: str | None = None,
    max_retries: int = 3,
    max_iterations: int = 8,
    directives: str | None = None,
    lazy_detection_interval: int = 10,
    keep_recent: int = 10,
    round_num: int = 1,
    runs_dir: Path | None = None,
    loop_strategy: Any | None = None,
    on_event: Any | None = None,
) -> dict:
    """Phase 1: run researcher agent + lazy detection + hypothesis registration.

    Returns::
        {"researcher_output": dict}
    """

    path = Path(workspace_path).resolve()
    if runs_dir is None:
        strategy_dir = path / "strategies" / strategy_name
        runs_dir = strategy_dir / "runs"
    else:
        runs_dir = Path(runs_dir)

    # Lazy detection
    if should_run_lazy_detection(round_num, lazy_detection_interval):
        lazy_results = []
        for agent_name in ("researcher", "factor_analyst", "strategist", "anti_overfit_analyst"):
            history = read_agent_history(
                runs_dir, agent_name, threshold=10,
                current_round=round_num, keep_recent=keep_recent,
            )
            if history:
                lazy_result = detect_lazy_behavior(
                    agent_name, history[-1].get("output", {}), history
                )
                lazy_results.append({"agent": agent_name, **lazy_result})
        if lazy_results:
            overall = sum(r.get("lazy_score", 0) for r in lazy_results) / len(lazy_results)
            save_laziness_report(run_dir, round_num, lazy_results, overall)

    # Spawn researcher
    state = {**current_state}
    if directives:
        state = {**state, "user_directives": directives}

    spawn = _make_spawn_fn(
        path, strategy_name, state, behavior, max_retries,
        max_iterations=max_iterations,
        loop_strategy=loop_strategy,
        on_event=on_event,
    )
    researcher_output = spawn("researcher", [])
    save_agent_record(run_dir, "researcher", 2, state, researcher_output)

    if session_id:
        _study_register_hypothesis(session_id, researcher_output, run_name)

    return {"researcher_output": researcher_output}


def run_execution_phase(
    workspace_path: Path | str,
    strategy_name: str,
    current_state: dict,
    researcher_output: dict,
    run_dir: Path,
    *,
    session_id: str | None = None,
    run_name: str = "",
    behavior: str | None = None,
    max_retries: int = 3,
    max_iterations: int = 8,
    inter_agent_sleep: float = 0.0,
    strategy_dir: Path | None = None,
    results_tsv: Path | None = None,
    round_num: int | None = None,
    loop_strategy: Any | None = None,
    on_event: Any | None = None,
) -> dict:
    """Phase 2: data_quality → factor_analyst → strategist → portfolio → backtest.

    Returns::

        {data_quality_output, factor_analyst_output, strategist_output,
         portfolio_construction_output, backtest_result, metrics, backtest_error}
    """
    from strategy_research.apps.autoresearch import save_agent_record
    from strategy_research.core.backtest import run_backtest_script

    path = Path(workspace_path).resolve()
    spawn = _make_spawn_fn(
        path, strategy_name, current_state, behavior, max_retries,
        max_iterations=max_iterations, inter_agent_sleep=inter_agent_sleep,
        loop_strategy=loop_strategy,
        on_event=on_event,
    )

    dq = spawn("data_quality", [researcher_output])
    save_agent_record(run_dir, "data_quality", 3, {"researcher": researcher_output}, dq)

    fa = spawn("factor_analyst", [researcher_output, dq])
    save_agent_record(run_dir, "factor_analyst", 3, {"researcher": researcher_output, "data_quality": dq}, fa)

    strat = spawn("strategist", [researcher_output, dq, fa])
    save_agent_record(run_dir, "strategist", 3, {"researcher": researcher_output, "factor_analyst": fa}, strat)

    pc = spawn("portfolio_construction", [strat])
    save_agent_record(run_dir, "portfolio_construction", 3, {"strategist": strat}, pc)

    backtest_result = run_backtest_script(
        workspace_path=path,
        strategy_name=strategy_name,
        action=strat.get("action", "unknown"),
        description=strat.get("hypothesis", ""),
        run_dir=run_dir,
        strategy_dir=strategy_dir,
        results_tsv=results_tsv,
        round_num=round_num,
    )

    backtest_error: str | None = None
    metrics: dict = {}
    if backtest_result.get("success"):
        metrics = backtest_result.get("metrics", {})
        if session_id:
            _study_append_evidence(session_id, run_name, metrics, strat)
    else:
        backtest_error = backtest_result.get("error", "unknown")

    return {
        "data_quality_output": dq,
        "factor_analyst_output": fa,
        "strategist_output": strat,
        "portfolio_construction_output": pc,
        "backtest_result": backtest_result,
        "metrics": metrics,
        "backtest_error": backtest_error,
    }


def run_evaluation_phase(
    workspace_path: Path | str,
    strategy_name: str,
    backtest_result: dict,
    metrics: dict,
    run_dir: Path,
    *,
    behavior: str | None = None,
    max_retries: int = 3,
    max_iterations: int = 8,
    acceptance_config=None,
    loop_strategy: Any | None = None,
    on_event: Any | None = None,
) -> dict:
    """Phase 3: risk_controller → attribution_analyst → anti_overfit → backtest_diag → decide.

    Returns::

        {risk_controller_output, attribution_analyst_output,
         anti_overfit_analyst_output, backtest_diagnostics_output,
         decision, verdict, aoa_llm_verdict}
    """
    from strategy_research.apps.autoresearch import save_agent_record
    from strategy_research.apps.strategy_acceptance import (
        decide as make_decision,
    )
    from strategy_research.apps.strategy_acceptance import (
        load_config as load_acceptance_config,
    )

    path = Path(workspace_path).resolve()
    spawn = _make_spawn_fn(
        path, strategy_name, {}, behavior, max_retries,
        max_iterations=max_iterations,
        loop_strategy=loop_strategy,
        on_event=on_event,
    )

    risk = spawn("risk_controller", [metrics])
    save_agent_record(run_dir, "risk_controller", 5, {"metrics": metrics}, risk)

    attr = spawn("attribution_analyst", [metrics, risk])
    save_agent_record(run_dir, "attribution_analyst", 5, {"metrics": metrics, "risk_controller": risk}, attr)

    aoa = spawn("anti_overfit_analyst", [metrics, risk, attr])
    save_agent_record(run_dir, "anti_overfit_analyst", 5, {"metrics": metrics, "risk_controller": risk, "attribution_analyst": attr}, aoa)

    diag = spawn("backtest_diagnostics", [backtest_result.get("run_log", ""), metrics])
    save_agent_record(run_dir, "backtest_diagnostics", 5, {"run_log": backtest_result.get("run_log", ""), "metrics": metrics}, diag)

    # Decide
    aoa_llm_verdict = None
    if isinstance(aoa, dict):
        aoa_llm_verdict = {
            "passed": bool(aoa.get("overfit_passed", False)),
            "score": float(aoa.get("overfit_score", 0.5) or 0.5),
            "reason": aoa.get("verdict_reason", ""),
            "concerns": aoa.get("methods_passed", []),
            "source": "anti_overfit_analyst",
        }

    if acceptance_config is None:
        acceptance_config = load_acceptance_config(
            workspace_config=path / "acceptance.yaml",
        )
    decision = make_decision(
        metrics=metrics,
        llm_verdict=aoa_llm_verdict,
        cfg=acceptance_config,
        stagnation_count=int(aoa.get("stagnation_count", 0) or 0) if isinstance(aoa, dict) else 0,
    )
    verdict = "keep" if decision.accept else "discard"

    return {
        "risk_controller_output": risk,
        "attribution_analyst_output": attr,
        "anti_overfit_analyst_output": aoa,
        "backtest_diagnostics_output": diag,
        "decision": decision,
        "verdict": verdict,
        "aoa_llm_verdict": aoa_llm_verdict,
    }
