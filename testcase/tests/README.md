# 归档测试索引（阶段 2a）

> 这些测试原本在 `tests/`，因只依赖将被移除的业务侧（`apps/`）而归档。
> **不被 pytest 收集**（`testpath = tests`）。保留原因：可作端到端样本与契约参考。

## 批次 2「R1 泳道」处置（2026-11-06）

R1 恢复 `apps/goal` 后，本目录 42 个 goal/workflow 测试按可达性分档处置：

| 档 | 数量 | 处置 | 去向 |
|---|---:|---|---|
| A | 27 | 改 `strategy_research.` → `agentnodes.` 后搬回 | `tests/`（**已实跑全绿**：511 passed / 3 skipped） |
| B | 含在 A | 同上 + `kernel.agent.builtin_tools.goal_tools` → `apps.builtin_tools.goal_tools` | `tests/`（2 个：`test_goal_tools` / `test_tool_display_goal`） |
| C | 10 | **不搬**，等 R2 恢复 L1 接口层 | 留在本目录，依赖清单见 `R2_PENDING.md` |
| D | 5 | **不搬**，永久不可达 | 留在本目录，见下方「档 D」 |

> 27 + 10 + 5 = 42，一一对应。下方索引表**未改动**（保持 2026-08-31 归档时的原始
> 快照价值）；搬回的文件仍列在其中，以本节的档位表为准。
>
> **与原清单的差异（实测修正）**：原计划档 A ~21 / 档 C 10。实际档 C 的 10 个里
> `test_goal_panel.py` 模块全在、28 用例全绿，**移入档 A**；而 `test_workflow_ownership.py`
> （原本不在 10 个枚举内）fixture 体内硬 import `routers/workflow.py`，8 用例全 ERROR，
> **补入档 C**。此外原 A 档枚举漏了 4 个无 import 期硬依赖的文件
> （`test_goal_p3b` / `test_goal_v2_session_decouple` / `test_workflow_checkpoint_resume` /
> `test_goal_workflow_state`），经 AST + 实跑确认可达后一并搬回。档 A 因此是 27 而非 21。

### 档 D —— 永久不可达（依赖批次 2/4/5 直删且无档案的域）

这 5 个文件 import 的包在减法批次中被**直接删除且不留档案**，
不存在「等某个后续批次恢复」的路径。保持归档，不搬回、不写 skip 占位：

| 文件 | 行数 | 永久阻塞依赖 | 删除批次 |
|---|---:|---|---|
| `test_goal_e2e.py` | 434 | `apps.config_runner`、`apps.db`、`kernel.agent.builtin_tools.data_tools` | 批次 5（数据域） |
| `test_goal_hook_p3d.py` | 309 | `apps.hypothesis`、`interfaces.cli.commands.autoresearch` | 批次 2（hypothesis）+ 后续 CLI 收敛 |
| `test_goal_scoping.py` | 205 | `apps.hypothesis`、`apps.hypothesis.store` | 批次 2（hypothesis） |
| `test_hypothesis_goal_linking.py` | 253 | `apps.hypothesis`（`HypothesisRegistry` / `HypothesisStore`） | 批次 2（hypothesis） |
| `test_workflow_monitor_and_skills.py` | 170 | `apps.backtest`、`kernel.agent.builtin_tools.workspace_tools` 的旧路径 | 批次 4（回测域） |

注：`apps/hypothesis` 的源码快照仍在 `testcase/golden/apps/hypothesis/`（6 文件 /
1,459 行），但那是**归档参考**，不在 `src/agentnodes/` 的 import 路径上；
`goal-capability-merge-design-20261105.md` §5.4 明确「本轮不恢复」。

### 档 D 的派生失败（已搬回文件内的单点 skip）

搬回的 A 档文件里有 3 个用例间接依赖上述直删域，跑不通且**不是等 A 流**：

| 文件::用例 | 阻塞原因 | 处置 |
|---|---|---|
| `tests/test_workflow_graph_shadow.py::test_shadow_alpha_research_matches` | builtin `alpha_research` 的 tool 节点用 `check_data` / `run_backtest`；实现模块随批次 1 删除 | `@pytest.mark.skip(reason=...)` |
| `tests/test_workflow_graph_shadow.py::test_shadow_data_quality_audit_matches` | 同上（`data_quality_audit` 只有 `check_data` 节点） | 同上 |
| `tests/test_workflow_resume_from_store.py::test_resume_from_store_returns_none_for_terminal_run` | 需要 `alpha_research` 跑到 `completed` 才能构造「终态」前置 | 同上 |

`node_types.register_builtin_tool_executors()` 实测注册 **0 个**执行器
（7 个候选工具名全部无实现），这是永久状态；模板文件
`src/agentnodes/templates/workflows/{alpha_research,data_quality_audit}.json`
仍在引用这两个已删工具，属待清理的悬空引用（**本批未动 `src/`**）。

### 新发现的同类问题（不在本批范围，供后续批次处理）

`tests/test_workflow_node_types.py::TestPythonTool::test_tool_wrapper_available`
断言 `register_builtin_tool_executors() > 0`，在同一批 1 删除后**同样恒为 0**。
该文件本批未修改（不在 42 个清单内），但它与上面 3 个 skip 同根因。
建议在恢复任何数据/回测能力时一并复核；若确认数据域永不复原，
该断言应改为 `>= 0` 或删除，并同步清理两个 builtin 模板的 `check_data` /
`run_backtest` 节点。

| 文件 | 行数 |
|---|---:|
| `test_acceptance_cli.py` | 213 |
| `test_agent_event_wiring.py` | 340 |
| `test_agent_loop_integration.py` | 168 |
| `test_agent_tools.py` | 683 |
| `test_all_tools.py` | 736 |
| `test_alpha_zoo.py` | 318 |
| `test_alpha_zoo_adapter.py` | 472 |
| `test_alpha_zoo_convert.py` | 86 |
| `test_alpha_zoo_convert_ast.py` | 356 |
| `test_alpha_zoo_convert_calls.py` | 199 |
| `test_alpha_zoo_convert_rules.py` | 330 |
| `test_alpha_zoo_convert_span_chain.py` | 236 |
| `test_alpha_zoo_convert_where.py` | 233 |
| `test_alpha_zoo_ops.py` | 378 |
| `test_anti_overfit_analyst.py` | 256 |
| `test_api_goal_router.py` | 574 |
| `test_approve_agent_loop.py` | 83 |
| `test_archive_race.py` | 192 |
| `test_attribution.py` | 86 |
| `test_attribution_comprehensive.py` | 203 |
| `test_autoresearch.py` | 432 |
| `test_autoresearch_helpers.py` | 188 |
| `test_autoresearch_phases.py` | 294 |
| `test_autoresearch_state.py` | 308 |
| `test_backtest.py` | 697 |
| `test_backtest_analysis_tools.py` | 384 |
| `test_backtest_contract_and_runs.py` | 246 |
| `test_backtest_engine_protocol.py` | 178 |
| `test_backtest_engine_wiring.py` | 165 |
| `test_backtest_metrics_contract.py` | 197 |
| `test_backtest_models_unified.py` | 79 |
| `test_backtest_run_script.py` | 220 |
| `test_bg_backtest.py` | 186 |
| `test_checkpoint_store.py` | 103 |
| `test_checkpoint_unification_b2.py` | 202 |
| `test_claims_tracking.py` | 333 |
| `test_cli_goal_cmd.py` | 171 |
| `test_cli_init.py` | 146 |
| `test_cli_merged.py` | 109 |
| `test_combiner.py` | 110 |
| `test_combo_tools.py` | 229 |
| `test_compute_factor_ops.py` | 311 |
| `test_compute_factor_parser.py` | 126 |
| `test_config_runner_factors.py` | 220 |
| `test_config_runner_import.py` | 363 |
| `test_config_runner_orchestration.py` | 238 |
| `test_config_runner_strategy.py` | 282 |
| `test_converter.py` | 420 |
| `test_covariance_ic.py` | 441 |
| `test_dag_planner.py` | 210 |
| `test_dag_planner_llm.py` | 154 |
| `test_dag_renderer.py` | 297 |
| `test_dag_view_widget.py` | 141 |
| `test_data_clean.py` | 320 |
| `test_data_clean_tool.py` | 161 |
| `test_data_import.py` | 83 |
| `test_data_import_local.py` | 209 |
| `test_data_import_orchestration.py` | 260 |
| `test_data_readiness.py` | 188 |
| `test_data_readiness_checks.py` | 186 |
| `test_data_readiness_edges.py` | 161 |
| `test_data_source_detect_market.py` | 52 |
| `test_data_source_registry.py` | 242 |
| `test_data_store_protocol.py` | 143 |
| `test_data_tools_unit.py` | 231 |
| `test_data_transforms.py` | 324 |
| `test_db_crud_contract.py` | 268 |
| `test_db_error_branches.py` | 216 |
| `test_db_execute_errors.py` | 207 |
| `test_db_persistence.py` | 92 |
| `test_db_read_filters.py` | 206 |
| `test_decide_wired.py` | 305 |
| `test_duckdb_store_adapter.py` | 124 |
| `test_duckdb_store_factors.py` | 106 |
| `test_e2e_full_flow.py` | 272 |
| `test_eastmoney_loader.py` | 187 |
| `test_engine_artifacts.py` | 137 |
| `test_engine_artifacts_basic.py` | 168 |
| `test_engine_artifacts_detailed.py` | 208 |
| `test_engine_base.py` | 308 |
| `test_engine_china_a_detailed.py` | 376 |
| `test_engine_china_futures_detailed.py` | 256 |
| `test_engine_cli_and_cleaning.py` | 255 |
| `test_engine_cli_e2e.py` | 135 |
| `test_engine_common.py` | 273 |
| `test_engine_common_helpers.py` | 283 |
| `test_engine_composite_detailed.py` | 357 |
| `test_engine_composite_onbar.py` | 299 |
| `test_engine_config.py` | 83 |
| `test_engine_crypto_detailed.py` | 517 |
| `test_engine_forex_detailed.py` | 529 |
| `test_engine_global_equity_detailed.py` | 110 |
| `test_engine_global_futures_detailed.py` | 125 |
| `test_engine_india_equity_detailed.py` | 140 |
| `test_engine_market_hooks.py` | 163 |
| `test_engine_markets.py` | 246 |
| `test_engine_models.py` | 214 |
| `test_engine_optimizers.py` | 156 |
| `test_engine_progress.py` | 105 |
| `test_engine_regression.py` | 301 |
| `test_engine_runner.py` | 252 |
| `test_engine_runner_e2e.py` | 310 |
| `test_engine_signals.py` | 63 |
| `test_engine_subengines.py` | 214 |
| `test_expansions.py` | 191 |
| `test_expression_evaluator.py` | 139 |
| `test_factor_registry_discipline.py` | 214 |
| `test_factor_tools_algorithms.py` | 197 |
| `test_factor_tools_execute.py` | 253 |
| `test_factor_validate.py` | 563 |
| `test_factor_validate_scores.py` | 231 |
| `test_file_cache.py` | 195 |
| `test_final_band_push.py` | 413 |
| `test_fundamental_mock.py` | 150 |
| `test_futures_base.py` | 253 |
| `test_gateway_singleton.py` | 82 |
| `test_goal.py` | 321 |
| `test_goal_account_usage.py` | 227 |
| `test_goal_cli.py` | 360 |
| `test_goal_context.py` | 378 |
| `test_goal_e2e.py` | 434 |
| `test_goal_event_bus.py` | 194 |
| `test_goal_events.py` | 297 |
| `test_goal_evidence_retry_idempotency.py` | 77 |
| `test_goal_hook_p3d.py` | 309 |
| `test_goal_injection_guard.py` | 133 |
| `test_goal_models.py` | 325 |
| `test_goal_p3b.py` | 283 |
| `test_goal_panel.py` | 243 |
| `test_goal_policy.py` | 140 |
| `test_goal_scoping.py` | 205 |
| `test_goal_store.py` | 575 |
| `test_goal_tools.py` | 558 |
| `test_goal_v2_session_decouple.py` | 162 |
| `test_goal_workflow.py` | 295 |
| `test_goal_workflow_phase3.py` | 404 |
| `test_goal_workflow_phase3b.py` | 247 |
| `test_goal_workflow_state.py` | 152 |
| `test_goal_workflow_v051.py` | 222 |
| `test_goal_workflow_v052.py` | 306 |
| `test_goal_workflow_v053.py` | 265 |
| `test_goal_workflow_v054.py` | 191 |
| `test_hanging_events.py` | 257 |
| `test_hard_threshold_rule.py` | 141 |
| `test_hitl_gate_graph.py` | 459 |
| `test_hitl_resume_rebuild.py` | 84 |
| `test_hypothesis.py` | 369 |
| `test_hypothesis_cli.py` | 297 |
| `test_hypothesis_goal_linking.py` | 253 |
| `test_hypothesis_ops.py` | 384 |
| `test_hypothesis_p3c.py` | 343 |
| `test_hypothesis_registry_sqlite.py` | 272 |
| `test_hypothesis_store.py` | 382 |
| `test_hypothesis_store_concurrent.py` | 178 |
| `test_hypothesis_validator.py` | 346 |
| `test_ic_utils.py` | 93 |
| `test_import_data_robustness.py` | 216 |
| `test_journal.py` | 179 |
| `test_lifespan_recover.py` | 82 |
| `test_llm_eval.py` | 188 |
| `test_load_ohlcv.py` | 314 |
| `test_market_data_cache_flow.py` | 193 |
| `test_market_mixin.py` | 122 |
| `test_mcp_data_tools.py` | 146 |
| `test_mcp_helpers.py` | 287 |
| `test_method_domain.py` | 83 |
| `test_metric_targets.py` | 152 |
| `test_monitor_phase.py` | 123 |
| `test_ohlcv_save.py` | 301 |
| `test_operators.py` | 1014 |
| `test_optimizer_degradation.py` | 221 |
| `test_optimizer_logging.py` | 238 |
| `test_p3_e2e.py` | 229 |
| `test_p3_integration.py` | 185 |
| `test_param_evolution.py` | 189 |
| `test_phase_engine_researcher_defense.py` | 48 |
| `test_phase_engine_round_outcome.py` | 97 |
| `test_portfolio.py` | 341 |
| `test_portfolio_correlation.py` | 97 |
| `test_portfolio_metrics.py` | 100 |
| `test_pr3_fixes.py` | 222 |
| `test_preflight.py` | 308 |
| `test_quant_correctness_secondary.py` | 163 |
| `test_r3_session_domain.py` | 207 |
| `test_r4_data_plane.py` | 174 |
| `test_r6_director.py` | 133 |
| `test_research_discipline.py` | 136 |
| `test_research_preset.py` | 306 |
| `test_risk_parity.py` | 113 |
| `test_run_api_coverage.py` | 235 |
| `test_run_card.py` | 277 |
| `test_run_equity_api.py` | 113 |
| `test_runner_aegis.py` | 225 |
| `test_runner_dag_helpers.py` | 104 |
| `test_runner_lifecycle.py` | 308 |
| `test_runner_load_graph.py` | 103 |
| `test_runner_monitor_check.py` | 150 |
| `test_scenario_router.py` | 196 |
| `test_schedule_api.py` | 371 |
| `test_schedule_to_study.py` | 238 |
| `test_scheduled_cli_runtime.py` | 284 |
| `test_scheduler_gateway_path.py` | 105 |
| `test_scheduler_recover_on_startup.py` | 167 |
| `test_scheduler_state_machine.py` | 258 |
| `test_scoreboard.py` | 89 |
| `test_search_symbol_tool.py` | 96 |
| `test_security_quickfix.py` | 96 |
| `test_spawn_agent_loop_strategy.py` | 99 |
| `test_stage3c_scheduler_migration.py` | 218 |
| `test_state_utils.py` | 101 |
| `test_strategy_acceptance.py` | 261 |
| `test_strategy_backfill.py` | 128 |
| `test_stream_and_combo.py` | 301 |
| `test_study_actions.py` | 869 |
| `test_study_api.py` | 923 |
| `test_study_budget.py` | 179 |
| `test_study_comprehensive.py` | 1458 |
| `test_study_concurrency.py` | 206 |
| `test_study_consistency_fixes.py` | 186 |
| `test_study_dump.py` | 234 |
| `test_study_e2e_api.py` | 315 |
| `test_study_e2e_integration.py` | 292 |
| `test_study_engine_dispatch.py` | 60 |
| `test_study_error_recovery.py` | 165 |
| `test_study_event_emit_session_id.py` | 136 |
| `test_study_executor.py` | 407 |
| `test_study_graph.py` | 335 |
| `test_study_guidance.py` | 345 |
| `test_study_hitl_respond.py` | 218 |
| `test_study_monitor.py` | 315 |
| `test_study_no_cycles.py` | 164 |
| `test_study_objective.py` | 288 |
| `test_study_p1_gaps.py` | 132 |
| `test_study_real_world.py` | 356 |
| `test_study_round_detail.py` | 218 |
| `test_study_rounds.py` | 87 |
| `test_study_run_round.py` | 153 |
| `test_study_runner_comprehensive.py` | 191 |
| `test_study_runner_integration.py` | 264 |
| `test_study_scheduler.py` | 640 |
| `test_study_store.py` | 492 |
| `test_study_store_finalization_ops.py` | 157 |
| `test_study_store_round_lifecycle.py` | 175 |
| `test_study_todos_knowledge.py` | 123 |
| `test_study_v2_micro_session.py` | 358 |
| `test_study_v2_path_params.py` | 266 |
| `test_study_v2_review_loop.py` | 268 |
| `test_study_v2_round_artifacts.py` | 331 |
| `test_swarm_presets_30.py` | 221 |
| `test_task_exception_logging.py` | 101 |
| `test_tier1_quickwins.py` | 167 |
| `test_tool_contract.py` | 173 |
| `test_tool_defensive_integration.py` | 96 |
| `test_tool_display_goal.py` | 100 |
| `test_tools_integration.py` | 401 |
| `test_v05_unit_checkpoint_events_dag.py` | 509 |
| `test_v05_unit_config.py` | 231 |
| `test_v05_unit_evaluator.py` | 284 |
| `test_v05_unit_hook.py` | 245 |
| `test_v05_unit_models.py` | 205 |
| `test_v05_unit_runner.py` | 238 |
| `test_v05_unit_runtime_context.py` | 450 |
| `test_validation_bootstrap_wf.py` | 121 |
| `test_validation_cli.py` | 207 |
| `test_validation_market.py` | 51 |
| `test_validation_monte_carlo.py` | 79 |
| `test_validation_runner.py` | 117 |
| `test_validation_trade_input.py` | 93 |
| `test_walk_forward.py` | 100 |
| `test_webui_e2e_playwright.py` | 377 |
| `test_workflow_approval_api.py` | 346 |
| `test_workflow_checkpoint_resume.py` | 196 |
| `test_workflow_config.py` | 222 |
| `test_workflow_control_api.py` | 215 |
| `test_workflow_graph_api.py` | 87 |
| `test_workflow_graph_shadow.py` | 294 |
| `test_workflow_hook.py` | 274 |
| `test_workflow_monitor_and_skills.py` | 170 |
| `test_workflow_ownership.py` | 262 |
| `test_workflow_resume_from_store.py` | 175 |
| `test_workflow_sse_stream.py` | 199 |
| `test_workspace_setup.py` | 196 |
| `test_yaml_evaluator.py` | 503 |

合计：**283 文件 / 69931 行**

## 复用建议

优先挑这几类做端到端样本（详见 `testcase/samples/`）：

- `test_config_runner_{import,strategy,factors,orchestration}.py` —— 配置驱动回测的完整链路
- `test_backtest{,_contract_and_runs,_run_script}.py` —— 回测执行与产物契约
- `test_compute_factor_{ops,parser}.py` / `test_operators.py` —— 因子算子语义（可当金标准）
- `test_data_source_{detect_market,registry}.py` + `test_eastmoney_loader.py` —— 8 loader 契约
- `test_study_*.py` —— HITL / 轮次 / 预算等 agent 编排的边界用例
- `test_goal_*.py` —— 目标驱动循环的边界用例
