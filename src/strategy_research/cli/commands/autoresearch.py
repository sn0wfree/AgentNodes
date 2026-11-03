"""Extracted from cli.py — autoresearch command and agent spawning logic.

Contains:
- cmd_autoresearch: automated research loop
- _spawn_agent: spawn a single agent (stub or real LLM)
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import yaml


def _append_backtest_evidence(
    session_id: str,
    run_name: str,
    metrics: dict,
    strategist_output: dict,
) -> None:
    """Append evidence for the active goal (P3-D3: autoresearch ↔ GoalStore).

    Failures are swallowed — the autoresearch loop must not break on evidence
    append failures.
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
        # Build a concise evidence text
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
                source_provider="autoresearch",
                source_type="backtest_run",
                artifact_path=strategist_output.get("hypothesis", "")[:200],
            ),
        )
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).debug(
            "append_backtest_evidence skipped: %s", exc,
        )


def _register_researcher_hypothesis(
    session_id: str,
    researcher_output: dict,
    run_name: str,
) -> None:
    """Register the researcher's hypothesis in the registry and link to active goal.

    Failures are swallowed — the autoresearch loop must not break on registry errors.
    """
    try:
        from strategy_research.core.goal import GoalStore
        from strategy_research.core.hypothesis import HypothesisRegistry

        thesis = researcher_output.get("hypothesis", "")
        if not thesis:
            return

        registry = HypothesisRegistry()
        # Check if a hypothesis with this title+thesis already exists (idempotent)
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

        # Link to active goal if present
        goal_store = GoalStore()
        goal = goal_store.get_current_goal(session_id)
        if goal is not None and hyp.goal_id != goal.goal_id:
            registry.link_goal(hyp.hypothesis_id, goal.goal_id)
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).debug(
            "register_researcher_hypothesis skipped: %s", exc,
        )


def _finalize_autoresearch_round(
    path: Path,
    strategy_name: str,
    metrics: dict,
    anti_overfit_analyst_output: dict,
    agent_outputs: dict,
    run_dir: Path,
    runs_dir: Path,
    round_num: int,
) -> tuple[str, Any]:
    """Step 6: decide verdict, patch results.tsv, save summary.

    Returns ``(verdict, decision)``.
    """
    from strategy_research.apps.autoresearch import (
        generate_run_summary,
        load_run_summary,
        save_run_summary,
    )
    from strategy_research.apps.strategy_acceptance import (
        decide as make_decision,
    )
    from strategy_research.apps.strategy_acceptance import (
        load_config as load_acceptance_config,
    )

    # Extract llm_verdict from anti_overfit_analyst output (if any)
    aoa_llm_verdict = None
    if isinstance(anti_overfit_analyst_output, dict):
        aoa_llm_verdict = {
            "passed": bool(anti_overfit_analyst_output.get("overfit_passed", False)),
            "score": float(anti_overfit_analyst_output.get("overfit_score", 0.5) or 0.5),
            "reason": anti_overfit_analyst_output.get("verdict_reason", ""),
            "concerns": anti_overfit_analyst_output.get("methods_passed", []),
            "source": "anti_overfit_analyst",
        }

    acceptance_cfg = load_acceptance_config(
        workspace_config=path / "acceptance.yaml",
    )
    decision = make_decision(
        metrics=metrics,
        llm_verdict=aoa_llm_verdict,
        cfg=acceptance_cfg,
        stagnation_count=int(anti_overfit_analyst_output.get("stagnation_count", 0) or 0)
            if isinstance(anti_overfit_analyst_output, dict) else 0,
    )

    verdict = "keep" if decision.accept else "discard"
    print(f"  decision.accept: {decision.accept}")
    print(f"  decision.reason: {decision.reason}")
    print(f"  decision.stagnation_triggered: {decision.stagnation_triggered}")
    print(f"  verdict: {verdict}")

    # Update results.tsv pending row → final verdict
    run_name = run_dir.name
    results_path = runs_dir / "results.tsv"
    if results_path.exists():
        content = results_path.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].startswith(run_name + "\t") or lines[i].startswith(run_name + " "):
                parts = lines[i].split("\t")
                if len(parts) >= 12:
                    parts[11] = verdict  # status
                    lines[i] = "\t".join(parts)
                break
        results_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Generate + save summary.json
    previous_summary = None
    if round_num > 1:
        prev_run_dir = runs_dir / f"run_{(round_num - 1):04d}"
        if prev_run_dir.exists():
            previous_summary = load_run_summary(prev_run_dir)

    summary = generate_run_summary(
        agent_outputs, metrics, verdict, round_num, previous_summary
    )
    summary["acceptance_decision"] = decision.to_dict()
    save_run_summary(run_dir, summary)
    print("  summary.json 已保存")
    return verdict, decision


def _run_cli_lazy_detection(
    round_num: int,
    lazy_detection_interval: int,
    keep_recent: int,
    runs_dir: Path,
    run_dir: Path,
) -> None:
    """Run lazy detection for the round (CLI variant with console output)."""
    from strategy_research.apps.autoresearch import (
        detect_lazy_behavior,
        read_agent_history,
        save_laziness_report,
        should_run_lazy_detection,
    )

    if not should_run_lazy_detection(round_num, lazy_detection_interval):
        return
    print(f"\n[Lazy Detection] 检测 Agent 行为 (每 {lazy_detection_interval} 轮)...")
    lazy_results = []

    for agent_name in ["researcher", "factor_analyst", "strategist", "anti_overfit_analyst"]:
        history = read_agent_history(
            runs_dir, agent_name, threshold=10,
            current_round=round_num, keep_recent=keep_recent,
        )
        if history:
            last_output = history[-1].get("output", {})
            lazy_result = detect_lazy_behavior(agent_name, last_output, history)
            lazy_results.append({"agent": agent_name, **lazy_result})
            if lazy_result["issues"]:
                print(f"  ⚠️ {agent_name}: {lazy_result['issues']}")

    if lazy_results:
        overall_score = sum(r.get("lazy_score", 0) for r in lazy_results) / len(lazy_results)
        save_laziness_report(run_dir, round_num, lazy_results, overall_score)
        print(f"✅ 保存 laziness report: {run_dir}/laziness_report.json")


def _run_execution_agents(
    path: Path, strategy_name: str, current_state: dict,
    researcher_output: dict, base_cooldown: float, jitter: float,
    min_cooldown: float, max_retries: int, run_dir: Path,
) -> tuple[dict, dict, dict, dict]:
    """Step 3: spawn execution agents (DQ → factor → strategist → portfolio)."""
    from strategy_research.apps.autoresearch import (
        get_cooldown_seconds,
        retry_agent_spawn,
        save_agent_record,
    )

    # Step 3: 执行 (强制执行所有 Agent)
    print("\n[Step 3] 执行...")

    # 3.1 Data Quality
    print("\n  [3.1] Data Quality...")
    cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
    print(f"    cooldown: {cooldown:.1f}s")
    time.sleep(cooldown)

    data_quality_output = retry_agent_spawn(
        lambda: _spawn_agent("data_quality", path, strategy_name, current_state, [researcher_output]),
        "data_quality",
        max_retries=max_retries,
    )
    save_agent_record(run_dir, "data_quality", 3, {"researcher": researcher_output}, data_quality_output)
    print(f"    passed: {data_quality_output.get('passed', '?')}")
    print(f"    warnings: {len(data_quality_output.get('warnings', []))}")

    # 3.2 Factor Analyst
    print("\n  [3.2] Factor Analyst...")
    cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
    print(f"    cooldown: {cooldown:.1f}s")
    time.sleep(cooldown)

    factor_analyst_output = retry_agent_spawn(
        lambda: _spawn_agent("factor_analyst", path, strategy_name, current_state, [researcher_output, data_quality_output]),
        "factor_analyst",
        max_retries=max_retries,
    )
    save_agent_record(run_dir, "factor_analyst", 3, {"researcher": researcher_output, "data_quality": data_quality_output}, factor_analyst_output)
    print(f"    candidates: {len(factor_analyst_output.get('candidates', []))}")
    print(f"    rejected: {len(factor_analyst_output.get('rejected', []))}")

    # 3.3 Strategist
    print("\n  [3.3] Strategist...")
    cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
    print(f"    cooldown: {cooldown:.1f}s")
    time.sleep(cooldown)

    strategist_output = retry_agent_spawn(
        lambda: _spawn_agent("strategist", path, strategy_name, current_state, [researcher_output, data_quality_output, factor_analyst_output]),
        "strategist",
        max_retries=max_retries,
    )
    save_agent_record(run_dir, "strategist", 3, {"researcher": researcher_output, "factor_analyst": factor_analyst_output}, strategist_output)
    print(f"    action: {strategist_output.get('action', '?')}")
    print(f"    changes: {len(strategist_output.get('changes', []))}")

    # 3.4 Portfolio Construction (强制执行)
    print("\n  [3.4] Portfolio Construction...")
    cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
    print(f"    cooldown: {cooldown:.1f}s")
    time.sleep(cooldown)

    portfolio_construction_output = retry_agent_spawn(
        lambda: _spawn_agent("portfolio_construction", path, strategy_name, current_state, [strategist_output]),
        "portfolio_construction",
        max_retries=max_retries,
    )
    save_agent_record(run_dir, "portfolio_construction", 3, {"strategist": strategist_output}, portfolio_construction_output)
    print(f"    method: {portfolio_construction_output.get('method', '?')}")
    print(f"    portfolio_vol: {portfolio_construction_output.get('portfolio_vol', '?')}")

    return (
        data_quality_output, factor_analyst_output,
        strategist_output, portfolio_construction_output,
    )


def _run_evaluation_agents(
    path: Path, strategy_name: str, current_state: dict,
    metrics: dict, backtest_result: dict, base_cooldown: float,
    jitter: float, min_cooldown: float, max_retries: int, run_dir: Path,
) -> tuple[dict, dict, dict, dict]:
    """Step 5: spawn evaluation agents (risk → attribution → anti-overfit → diag)."""
    from strategy_research.apps.autoresearch import (
        get_cooldown_seconds,
        retry_agent_spawn,
        save_agent_record,
    )

    # Step 5: 评估 (强制执行所有 Agent)
    print("\n[Step 5] 评估...")

    # 5.1 Risk Controller
    print("\n  [5.1] Risk Controller...")
    cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
    print(f"    cooldown: {cooldown:.1f}s")
    time.sleep(cooldown)

    risk_controller_output = retry_agent_spawn(
        lambda: _spawn_agent("risk_controller", path, strategy_name, current_state, [metrics]),
        "risk_controller",
        max_retries=max_retries,
    )
    save_agent_record(run_dir, "risk_controller", 5, {"metrics": metrics}, risk_controller_output)
    print(f"    risk_passed: {risk_controller_output.get('risk_passed', '?')}")
    print(f"    risk_rating: {risk_controller_output.get('risk_rating', '?')}")

    # 5.2 Attribution Analyst
    print("\n  [5.2] Attribution Analyst...")
    cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
    print(f"    cooldown: {cooldown:.1f}s")
    time.sleep(cooldown)

    attribution_analyst_output = retry_agent_spawn(
        lambda: _spawn_agent("attribution_analyst", path, strategy_name, current_state, [metrics, risk_controller_output]),
        "attribution_analyst",
        max_retries=max_retries,
    )
    save_agent_record(run_dir, "attribution_analyst", 5, {"metrics": metrics, "risk_controller": risk_controller_output}, attribution_analyst_output)
    print(f"    alpha: {attribution_analyst_output.get('alpha', '?')}")
    print(f"    beta_mkt: {attribution_analyst_output.get('beta_mkt', '?')}")

    # 5.3 Anti-overfit Analyst
    print("\n  [5.3] Anti-overfit Analyst...")
    cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
    print(f"    cooldown: {cooldown:.1f}s")
    time.sleep(cooldown)

    anti_overfit_analyst_output = retry_agent_spawn(
        lambda: _spawn_agent("anti_overfit_analyst", path, strategy_name, current_state, [metrics, risk_controller_output, attribution_analyst_output]),
        "anti_overfit_analyst",
        max_retries=max_retries,
    )
    save_agent_record(run_dir, "anti_overfit_analyst", 5, {"metrics": metrics, "risk_controller": risk_controller_output, "attribution_analyst": attribution_analyst_output}, anti_overfit_analyst_output)
    print(f"    verdict: {anti_overfit_analyst_output.get('verdict', '?')}")
    print(f"    overfit_passed: {anti_overfit_analyst_output.get('overfit_passed', '?')}")

    # 5.4 Backtest Diagnostics (强制执行)
    print("\n  [5.4] Backtest Diagnostics...")
    cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
    print(f"    cooldown: {cooldown:.1f}s")
    time.sleep(cooldown)

    backtest_diagnostics_output = retry_agent_spawn(
        lambda: _spawn_agent("backtest_diagnostics", path, strategy_name, current_state, [backtest_result.get("run_log", ""), metrics]),
        "backtest_diagnostics",
        max_retries=max_retries,
    )
    save_agent_record(run_dir, "backtest_diagnostics", 5, {"run_log": backtest_result.get("run_log", ""), "metrics": metrics}, backtest_diagnostics_output)
    print(f"    error_type: {backtest_diagnostics_output.get('error_type', '?')}")
    print(f"    severity: {backtest_diagnostics_output.get('severity', '?')}")

    return (
        risk_controller_output, attribution_analyst_output,
        anti_overfit_analyst_output, backtest_diagnostics_output,
    )


def cmd_autoresearch(args: argparse.Namespace) -> int:
    """执行 autoresearch 命令 - 运行自动化研究循环。"""
    from strategy_research.apps.autoresearch import (
        DEFAULT_KEEP_RECENT,
        get_cooldown_seconds,
        read_current_state,
        retry_agent_spawn,
        save_agent_record,
    )
    from strategy_research.core.backtest import run_backtest_script

    path = Path(args.path).resolve()

    # 检查工作区
    if not (path / "config.yaml").exists():
        print(f"❌ 不是有效的工作区: {path}")
        return 1

    # 读取 config.yaml
    config_path = path / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    strategy_name = args.strategy
    if not strategy_name:
        strategy_name = config.get("workspace", {}).get("default_strategy")
        if not strategy_name:
            print("❌ 未指定策略名称，请使用 --strategy <name>")
            return 1

    # 速度控制参数
    base_cooldown = args.cooldown or 30.0
    jitter = args.jitter or 10.0
    min_cooldown = args.min_cooldown or 1.0
    max_retries = args.max_retries or 3

    print("\n🚀 启动 autoresearch 循环")
    print(f"   策略: {strategy_name}")
    print(f"   cooldown: {base_cooldown}s ± {jitter}s (MIN={min_cooldown}s)")
    print(f"   max_retries: {max_retries}")
    print()

    # 主循环
    round_num = 0
    while True:
        round_num += 1
        round_start = time.time()

        print(f"{'='*60}")
        print(f"📍 第 {round_num} 轮研究")
        print(f"{'='*60}")

        # Step 1: 读状态
        print("\n[Step 1] 读取状态...")
        current_state = read_current_state(path, strategy_name)
        print(f"  最佳 Calmar: {current_state['best_calmar']:.4f}")
        print(f"  总轮数: {current_state['total_runs']}")

        # 创建 run 目录 (提前创建,避免 lazy detection 时重复创建)
        runs_dir = path / "strategies" / strategy_name / "runs"
        # 使用 max(num) + 1 与 backtest 模块保持一致
        existing_nums = []
        for d in runs_dir.iterdir():
            if d.is_dir() and d.name.startswith("run_"):
                try:
                    existing_nums.append(int(d.name.split("_")[1]))
                except (ValueError, IndexError):
                    pass
        run_num = max(existing_nums, default=0) + 1
        run_name = f"run_{run_num:04d}"
        run_dir = runs_dir / run_name
        run_dir.mkdir(exist_ok=True)
        (run_dir / "agents").mkdir(exist_ok=True)

        # Lazy Detection (每 N 轮检测)
        lazy_detection_interval = args.lazy_detection_interval or 10
        keep_recent = args.keep_recent or DEFAULT_KEEP_RECENT
        _run_cli_lazy_detection(
            round_num, lazy_detection_interval, keep_recent, runs_dir, run_dir
        )

        # Step 2: spawn Researcher
        print("\n[Step 2] spawn Researcher...")
        researcher_output = retry_agent_spawn(
            lambda: _spawn_agent("researcher", path, strategy_name, current_state, []),
            "researcher",
            max_retries=max_retries,
        )
        save_agent_record(run_dir, "researcher", 2, current_state, researcher_output)
        print(f"  action: {researcher_output.get('action', '?')}")
        print(f"  hypothesis: {researcher_output.get('hypothesis', '?')[:50]}...")

        # P3-D3: Register researcher hypothesis in registry + link to active goal
        _register_researcher_hypothesis(
            session_id=f"autoresearch-{strategy_name}",
            researcher_output=researcher_output,
            run_name=run_name,
        )

        # Step 3: 执行 (强制执行所有 Agent)
        (data_quality_output, factor_analyst_output,
         strategist_output, portfolio_construction_output) = _run_execution_agents(
            path, strategy_name, current_state, researcher_output,
            base_cooldown, jitter, min_cooldown, max_retries, run_dir,
        )
        # Step 4: 运行回测
        print("\n[Step 4] 运行回测...")
        cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
        print(f"  cooldown: {cooldown:.1f}s")
        time.sleep(cooldown)

        backtest_result = run_backtest_script(
            workspace_path=path,
            strategy_name=strategy_name,
            action=strategist_output.get("action", "unknown"),
            description=strategist_output.get("hypothesis", ""),
            run_dir=run_dir,  # 使用已创建的 run_dir,避免创建额外的空目录
        )

        if backtest_result.get("success"):
            metrics = backtest_result.get("metrics", {})
            print(f"  Calmar: {metrics.get('calmar', 'N/A')}")
            print(f"  Sharpe: {metrics.get('sharpe', 'N/A')}")
            print(f"  MaxDD: {metrics.get('max_dd', 'N/A')}")

            # P3-D3: Append goal evidence for active session goal
            _append_backtest_evidence(
                session_id=f"autoresearch-{strategy_name}",
                run_name=run_name,
                metrics=metrics,
                strategist_output=strategist_output,
            )
        else:
            print(f"  ❌ 回测失败: {backtest_result.get('error', 'unknown')}")
            metrics = {}

        # Step 5: 评估 (强制执行所有 Agent)
        (risk_controller_output, attribution_analyst_output,
         anti_overfit_analyst_output, backtest_diagnostics_output) = _run_evaluation_agents(
            path, strategy_name, current_state, metrics, backtest_result,
            base_cooldown, jitter, min_cooldown, max_retries, run_dir,
        )
        # Step 6: 提交
        print("\n[Step 6] 提交...")

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
        verdict, decision = _finalize_autoresearch_round(
            path, strategy_name, metrics, anti_overfit_analyst_output,
            agent_outputs, run_dir, runs_dir, round_num,
        )

        print(f"\n✅ 第 {round_num} 轮完成 ({run_name})")
        print(f"  verdict: {verdict}")
        print(f"  Calmar: {metrics.get('calmar', 'N/A')}")
        print(f"  Sharpe: {metrics.get('sharpe', 'N/A')}")
        print(f"  MaxDD: {metrics.get('max_dd', 'N/A')}")

        # 检查停止条件
        if args.max_rounds and round_num >= args.max_rounds:
            print(f"\n🛑 达到最大轮数 ({args.max_rounds}),停止")
            break

        # Phase C-2: stagnation stop — 连续 N 轮 reject 触发自动停止
        if decision.stagnation_triggered:
            print(f"\n🛑 stagnation 触发 ({decision.reason}), 停止 autoresearch")
            break

        # 轮间 cooldown
        round_time = time.time() - round_start
        round_cooldown = get_cooldown_seconds(base_cooldown * 2, jitter * 2, min_cooldown * 2)
        if round_time < round_cooldown:
            wait_time = round_cooldown - round_time
            print(f"\n⏳ 轮间 cooldown: {wait_time:.1f}s")
            time.sleep(wait_time)

    return 0


def _spawn_agent(agent_name: str, workspace_path: Path, strategy_name: str,
                 current_state: dict, previous_outputs: list) -> str:
    """spawn 单个 Agent (Phase C-1: 真接 AgentLoop + 角色 prompt).

    Thin delegating wrapper around ``core.autoresearch.spawn_agent`` — the
    full body (real-LLM path + stub modes) was moved there so the study
    executor can share it without re-importing the CLI module. Behavior is
    identical to the previous inline version.

    stub 行为由环境变量 ``AUTORESEARCH_BEHAVIOR`` 控制:
    - "static": 每次返回相同输出 (默认,用于测试)
    - "varying": 每次返回不同输出 (模拟真实 Agent 探索)
    - "improving": 模拟 Agent 找到改进方案的过程

    返回: JSON 字符串, 调用方 parse_agent_output() 解码.
    """
    from strategy_research.apps.autoresearch import spawn_agent

    return spawn_agent(
        agent_name, workspace_path, strategy_name,
        current_state, previous_outputs,
    )
