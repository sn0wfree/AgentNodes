# 批次 4 设计：移除回测与验证域

> 状态：**设计（待实施）** · 2026-11-05
> 上游：`docs/design/subtraction-roadmap-v2-20261105.md` 批次 4
> 起点：`5cf82eec`（批次 3 完成，L2 16,284 行 / 75 文件）

---

## 0. 结论先行

| 问题 | 答案 |
|---|---|
| 删多少 | **35 文件 / 5,982 行** |
| L3 依赖 | **0 条** —— kernel 完全不 import 回测 |
| L1 依赖 | 9 条（`cli/__init__.py` 8 + `routers/run.py` 1） |
| L2 依赖 | 4 条（全在 `capabilities.py` 注册门面） |
| 删几个 capability kind | **4 个**（不是 8 个，见 §2） |
| RSI 闭环受影响吗 | **不受影响**（golden_eval 的 runner 是 LLM 评测） |
| 需要安全点吗 | **不需要** —— 没有通用件混在里面 |

---

## 1. 删除范围

| 对象 | 文件 | 行数 | 业务密度 | 内容 |
|---|---:|---:|---:|---|
| `apps/engine/` | 21 | 2,350 | 26.7% | 多市场回测引擎（china_futures / global_equity / india_equity …） |
| `apps/strategy_acceptance/` | 4 | 883 | 18.0% | 验收规则 + LLM 评测 + CLI |
| `apps/backtest.py` | 1 | 811 | 37.5% | 回测入口（run_backtest_script / get_experiment_history） |
| `apps/config_runner.py` | 1 | 605 | 47.3% | YAML 配置驱动（load_yaml_config / load_data） |
| `apps/factor_validate.py` | 1 | 581 | 27.4% | 因子验证（IC / IR / 6 维评分） |
| `apps/backtest_engine/` | 5 | 498 | 39.4% | 引擎抽象层 |
| `apps/run_card.py` | 1 | 242 | 14.0% | 回测结果卡片 |
| `apps/backtest_models.py` | 1 | 12 | 66.7% | 数据类 |
| **合计** | **35** | **5,982** | — | |

---

## 2. capability kind：只删 4 个（AST 精确核算）

我用 AST 提取每个工厂函数的**真实** import（不靠正则窗口——第一版探测把相邻函数的
import 算进来了，虚报成 8 个）：

| kind | 工厂 | 真实实现位置 | 批次 4 删？ |
|---|---|---|---|
| `backtest.from_yaml` | `_run_backtest_from_yaml` | `apps.backtest` | **删** |
| `backtest.script` | `_run_backtest_script` | `apps.backtest` | **删** |
| `backtest.decide` | `_strategy_decide` | `apps.strategy_acceptance` | **删** |
| `config.runner` | `_config_runner` | `apps.config_runner` | **删** |
| `data.clean` | `_data_clean` | `apps.tools.data_clean` | 留（批次 5） |
| `data.readiness` | `_check_data_readiness` | `apps.data_readiness` | 留（批次 5） |
| `datasource.registry` | `_data_source_registry` | `apps.data_source.registry` | 留（批次 5） |
| `datasource.base` | `_data_source_base` | `apps.data_source.base` | 留（批次 5） |
| `datasource.detect_market` | `_detect_market` | `apps.data_source.utils` | 留（批次 5） |
| 其余 17 个 | — | `db` / `compute_factor` / `evolution` / `tools` | 留 |

**能力注册 26 → 22 kind。**

---

## 3. 删掉这 4 个 kind 后，谁还会 resolve 它们（5 处，实测）

| 位置 | 用法 | 处置 |
|---|---|---|
| `kernel/agent/exec_registry.py:78` | `_backtest_executor` 闭包内 resolve `backtest.script` | **删这个 executor 注册**（它本身就是回测业务） |
| `kernel/agent/exec_registry.py:94` | `_decide_executor` resolve `backtest.decide` | **删** |
| `kernel/swarm/runtime.py:871` | 同上（SwarmRuntime 版） | **删** |
| `kernel/swarm/runtime.py:888` | 同上 | **删** |
| `kernel/agent/builtin_tools/data_tools.py:717` | `resolve("config.runner").load_yaml_config`（`source='config'` 路径） | **显式降级**（见 §4） |

前 4 处是「注册了 executor，调用时才 resolve」——kind 消失后调用会抛
`CapabilityNotFound`。但这些 executor **本身就是回测业务**，删掉是对的。

---

## 4. `data_tools.py:717` 的显式降级（不静默跳过）

```python
if source == "config":
    cfg_path = base / "config.yaml"
    if cfg_path.exists():
        from strategy_research.kernel.agent.capability import resolve
        load_yaml_config = resolve("config.runner").load_yaml_config   # ← 会抛
        ...
```

**改法**：`source='config'` 已无实现，给出可操作错误而不是 `CapabilityNotFound`：

```python
if source == "config":
    return err_actionable(
        "source='config' is no longer available",
        received=strategy_name,
        expected="source='explicit' with codes/start_date/end_date",
        fix="YAML config-driven data loading was removed in subtraction batch 4. "
            "Pass codes/start_date/end_date explicitly.",
        tool="check_data",
    )
```

**为什么不是静默 `cfg = None`**：那会让 `check_data` 在无 config 的情况下
继续跑，用户以为读到了配置。明确报错才能让人改调用方式。

---

## 5. L1 出口处置

### 5.1 `interfaces/api/routers/run.py`（177 行）—— 整个删除

它唯一的职能就是 `run_backtest_script`（`/api/run` 路由）。删掉后
`app.py` 反注册该 router。

### 5.2 `interfaces/cli/__init__.py` 的 8 个子命令

| 子命令 | 处置 |
|---|---|
| `run` | 删（`cmd_run` → `run_backtest_script`） |
| `evaluate` | 删（`run_backtest_script` 的复跑） |
| `list` | 删（`get_experiment_history`） |
| `validate` | 删（`factor_validate`） |
| `backtest` | 删（`commands/merged.py::add_backtest_parser`） |
| `check` | 删（同上 `add_check_parser`） |
| `engine` | 删（`apps.engine.cli`） |
| `accept` | 删（`apps.strategy_acceptance.cli`） |

**保留**：`init` / `status` / `reproduce` / `preflight` / `import` / `session` /
`skills` / `swarm` / `mcp` / `schedule` / `api` / `webui` / `serve` / `compact` / `echo` / `llm`

> `reproduce` 依赖 `config.yaml` + `strategies/<name>/runs/` 目录结构 —— 这是
> 研究工作区的概念。它不 import 批次 4 的包（只是文件操作），但语义上属业务。
> **本批次保留**（不在删除清单里），批次 7（templates 去研究化）再评估。

### 5.3 `cli/commands/merged.py`（部分）

`add_backtest_parser` / `add_check_parser` / `dispatch_backtest` / `dispatch_check`
删除；`dispatch_serve` 保留。

---

## 6. `apps/capabilities.py` 的改动

删 4 个工厂函数 + 4 个 kind：
- `_run_backtest_from_yaml`
- `_run_backtest_script`
- `_strategy_decide`
- `_config_runner`

保留 `_data_clean` / `_check_data_readiness` / `_data_source_*`（批次 5 再删）。

---

## 7. 测试处置

按批次 3 的方法：先跑门禁拿清单，再按文件批量归档。

**预判归档清单**（实施时以门禁实测为准）：

| 文件 | 行数 | 理由 |
|---|---:|---|
| `test_backtest.py` | 已归档（批次 2） | — |
| `test_backtest_contract_and_runs.py` | 已归档 | — |
| `test_backtest_metrics_contract.py` | 已归档 | — |
| `test_backtest_run_script.py` | 已归档 | — |
| `test_config_runner_*.py`（4 个） | 已归档 | — |
| `test_engine_*.py`（16 个） | 已归档 | — |
| `test_strategy_acceptance.py` | 已归档 | — |
| `test_factor_validate*.py`（2 个） | 已归档 | — |
| `test_acceptance_cli.py` | 已归档 | — |
| `test_run_card.py` | 已归档 | — |
| `test_quant_correctness_secondary.py` | 231（在 tests/） | **新增归档** |
| `test_prepare_no_lookahead.py` | ~60（在 tests/） | **新增归档** |
| `test_e2e_full_flow.py` | ~200（在 tests/） | 需逐项看 |
| `test_bg_backtest.py` | 已归档 | — |

> 批次 1/2 已经归档了大部分回测测试。批次 4 主要影响的是
> `tests/` 里还没归档的那几个（`test_quant_correctness_secondary` 等）。

---

## 8. 执行顺序

```
1. 本设计文档（doc first）                          ← 当前
2. 门禁先跑 —— check_dangling_imports 拿精确清单
3. 删 apps 35 文件
4. 删 kernel 的 4 处业务 executor（exec_registry × 2 + swarm × 2）
5. 改 data_tools.py:717 为显式降级
6. 删 L1 出口（routers/run.py 整删 + cli 8 子命令 + merged.py 部分）
7. 改 capabilities.py（26 → 22 kind）
8. 归档测试 + 收集 0 errors
9. 全量回归（与批次 3 的 49 failed 差分）
10. sync_architecture_diagram.py --write + 版本史 v10.1
11. commit
```

**没有安全点**：本批次不含任何通用件（不像批次 3 的 Ticket/Assignment）。
所有删除对象都是纯业务。

---

## 9. 预期效果

| 指标 | 批次 3 后 | 批次 4 后 |
|---|---:|---:|
| L2 行数 | 16,284 | **~10,300（-37%）** |
| L2 文件 | 75 | **~40** |
| L1 行数 | 23,863 | ~21,000 |
| 总计 | 88,771 | **~82,800** |
| 跨层边 L1→L2 | 36 | ~27 |
| 跨层边 L2→L3 | 6 | ~6（回测不 import kernel） |
| 能力 kind | 26 | **22** |

---

## 10. 不在本批次范围

| 项 | 归属 |
|---|---|
| `apps/evolution` 的 golden_eval runner 注入化 | 批次 8 |
| `builtin_tools/{backtest,backtest_analysis,factor,data_clean}_tools.py` 移出 L3 | 批次 6 |
| `data_tools.py` 的 `datasource.*` / `data.readiness` 降级 | 批次 5 |
| `apps/preflight.py` / `workspace_setup.py` | 批次 5 |
| `reproduce` / `import` 子命令的语义 | 批次 7 |
