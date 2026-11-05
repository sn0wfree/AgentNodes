# Runner.py Refactoring Plan

> Date: 2026-08-20
> 目标: 将 runner.py 从 ~2200 行重构为 ~850 行（模块化提取）

## 1. 提取模块

### 1.1 engine_common.py（共享工具）

**来源**: runner.py 1708-1823 + 新增

| 函数 | 说明 |
|------|------|
| `build_agent_ctx(strategy, run_dir, session, runner)` | 构建 agent 上下文 dict |
| `safe_json_loads(text, fallback=None)` | JSON 解析，支持 markdown fences |
| `phase_emitter(session, sid, round_num, phase)` | 上下文管理器，自动 emit started/done |
| `save_agent_outputs(runner, run_dir, agent_outputs)` | 统一 agent output 保存 |

### 1.2 phase_engine.py（Phase 引擎）

**来源**: runner.py 820-1147 + 1397-1475

| 函数 | 说明 |
|------|------|
| `run_round_phases(runner, path, strategy, ...)` | Phase 引擎主入口 |
| `run_researcher_with_retry(...)` | researcher 阶段 |
| `run_execution_with_parse_retry(...)` | execution 阶段 |
| `run_evaluation_with_gates(...)` | evaluation + guidance gates |

### 1.3 dag_engine.py（DAG 引擎）

**来源**: runner.py 1505-1604

| 函数 | 说明 |
|------|------|
| `run_round_dag(runner, path, strategy, ...)` | DAG 引擎主入口 |

### 1.4 monitor.py（监控逻辑）

**来源**: runner.py 634-819

| 函数 | 说明 |
|------|------|
| `run_monitor_phase(runner, ...)` | 监控阶段主逻辑 |
| `repair_monitor_rounds(...)` | 修复监控轮次 |
| `run_monitor_check(...)` | 执行监控检查 |
| `monitor_sleep(...)` | 监控间隔睡眠 |

### 1.5 metric_targets.py（指标工具）

**来源**: runner.py 87-176

| 函数 | 说明 |
|------|------|
| `meets_metric_targets(metrics, targets)` | 检查指标是否达标 |
| `metric_pass_set(metrics, targets)` | 返回通过的指标集合 |
| `acceptance_config_from_targets(targets)` | 构建验收配置 |

## 2. 修改文件清单

| 文件 | 操作 |
|------|------|
| `core/study/engine_common.py` | **新建**：共享工具 |
| `core/study/phase_engine.py` | **新建**：Phase 引擎 |
| `core/study/dag_engine.py` | **新建**：DAG 引擎 |
| `core/study/monitor.py` | **新建**：监控逻辑 |
| `core/study/metric_targets.py` | **新建**：指标工具 |
| `core/study/runner.py` | **重构**：删除提取的代码，改为 import |
| `core/study/langgraph_engine.py` | **更新**：使用 engine_common 共享工具 |

## 3. 验证

- 每个模块提取后运行测试
- 最终 72 tests 全部通过
- runner.py 行数从 ~2200 降至 ~850
