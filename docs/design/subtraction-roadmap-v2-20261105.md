# AgentNodes 减法路线图 v2（2026-11-05 重新规划）

> 取代 `docs/design/agentnodes-split-plan-20261104.md` 的阶段 2 执行顺序。
> 那份文档定义了**边界**（哪些归框架、哪些归业务），本文件定义**顺序**。
>
> 起点：`411a7ee7`（批次 3 完成，L2 16,284 行 / 75 文件）。

---

## 0. 已经走了多远

| 批次 | 内容 | 提交 | 删除行数 | L2 剩余 |
|---|---|---|---:|---:|
| 1 | `apps/alpha_zoo`（468 文件） | `c72f0c6e` | 27,263 | 38,922 |
| 2 | `goal` / `hypothesis` / `validation` / `portfolio` / `export` | `08303f04` | 9,328 | 29,679 |
| — | 门禁修复 + 架构图 `--write` | `749fa1fc` | — | 29,679 |
| 3 | `study` / `autoresearch`（Ticket 提取到 L3） | `c69e1fde` `411a7ee7` | ~12,000 | **16,284** |

**累计删除 48,600 行 / 原 L2 的 75%。**

关键结构成果：

| 指标 | 起点 | 现在 |
|---|---:|---:|
| L2 行数 | 66,100 | **16,284（-75%）** |
| L2 文件 | 625 | **75** |
| 跨层边 L1→L2 | 171 | **36** |
| 跨层边 L2→L3 | 39 | **6** |
| 上行违规 | 55 | **2**（L3→L1 2 · **L2→L1 0** · L3→L2 0） |

---

## 1. 剩余 L2 的全量盘点（AST + 词频实测）

| 包/文件 | 文件 | 行数 | 业务词密度 | 判定 |
|---|---:|---:|---:|---|
| `engine/` | 21 | 2,350 | **26.7%** | 业务（多市场回测引擎） |
| `data_source/` | 13 | 2,141 | 9.4% | 业务（8 个行情 loader） |
| **`evolution/`** | 11 | **1,916** | **1.5%** | **框架机制（RSI 闭环）** |
| `compute_factor.py` | 1 | 1,388 | 7.1% | 业务（因子算子） |
| `db.py` | 1 | 1,273 | **23.3%** | 业务（DuckDB OHLCV 表） |
| `strategy_acceptance/` | 4 | 883 | **18.0%** | 业务（验收规则） |
| `tools/` | 4 | 822 | 8.5% | 业务（数据清洗/变换） |
| `backtest.py` | 1 | 811 | **37.5%** | 业务（回测入口） |
| `config_runner.py` | 1 | 605 | **47.3%** | 业务（YAML 配置驱动） |
| `factor_validate.py` | 1 | 581 | **27.4%** | 业务（因子验证） |
| `backtest_engine/` | 5 | 498 | **39.4%** | 业务（引擎抽象） |
| `data_import.py` | 1 | 496 | **28.2%** | 业务（数据导入） |
| `data_readiness.py` | 1 | 426 | 13.1% | 业务（数据就绪检查） |
| `duckdb_store.py` | 1 | 407 | **29.2%** | 业务（DuckDB 适配） |
| `capabilities.py` | 1 | 296 | 24.7% | **注册门面（混合）** |
| `preflight.py` | 1 | 276 | 12.3% | 业务（环境检查） |
| `run_card.py` | 1 | 242 | 14.0% | 业务（回测卡片） |
| `workspace_setup.py` | 1 | 168 | 1.8% | 业务（工作区脚手架） |
| `data_store_registry.py` | 1 | 73 | 17.8% | 混合（DataStore 注册） |
| `backtest_models.py` | 1 | 12 | 66.7% | 业务（数据类） |
| **合计** | **72** | **15,664** | — | |

L2 另有 `templates/`（2 文件 + 68 个 .md，526 行代码 + 提示词）。

L3 里还有 **6,776 行业务工具** 住在 `kernel/agent/builtin_tools/`：

| 文件 | 行 | 业务词 | 框架词 | 判定 |
|---|---:|---:|---:|---|
| `factor_tools.py` | 1,164 | 395 | 208 | **业务** |
| `data_tools.py` | 773 | 151 | 177 | **业务** |
| `backtest_tools.py` | 513 | 124 | 110 | **业务** |
| `backtest_analysis_tools.py` | 450 | 156 | 82 | **业务** |
| `data_clean_tools.py` | 174 | 41 | 40 | **业务** |
| `display_tools.py` | 533 | 56 | 133 | 框架（工具结果渲染） |
| `goal_tools.py` | 582 | 9 | 136 | **框架**（但 goal 已删 → 死代码） |
| `workspace_tools.py` | 415 | 16 | 131 | 框架 |
| `file_tools.py` | 369 | 13 | 129 | 框架 |
| `subagent_tool.py` | 298 | 13 | 260 | 框架 |
| `bg_tools.py` | 242 | 11 | 55 | 框架 |
| `web_tools.py` | 219 | 10 | 86 | 框架 |
| `todo_tools.py` | 218 | 1 | 107 | 框架 |
| `help_tools.py` | 142 | 4 | 53 | 框架 |
| `options_tools.py` | 121 | 5 | 16 | 框架 |
| `shell_tools.py` | 175 | 1 | 69 | 框架 |
| `utils.py` | 190 | 1 | 41 | 框架 |
| `__init__.py` | 198 | 47 | 193 | 框架（注册门面） |
| **合计** | **6,776** | — | — | 业务 5 文件 **3,074 行** |

---

## 2. 三个关键判断（本次规划的实质内容）

### 2.1 `apps/evolution` 留下，而且是核心

**证据**（AST 实测）：

- 业务词密度 **1.5%** —— 全场最低（对照 `config_runner.py` 47.3%）
- 对**所有待删业务包**的依赖：**0**
- 它的 11 个文件只依赖 `foundation.storage.holdout` · `kernel.agent.capability` ·
  `kernel.orchestration.genome_runtime` · 以及自己内部
- 它的验证信号来自 `evals/`（仓库根的独立包，7 文件 ~2,100 行），
  不是来自 `apps/backtest`

**结论**：RSI 闭环是**框架的一等能力**（用户目标：「变成一个可以自我进化的 agent」）。
它不该跟业务包一起删。但需要一次**去研究化改造**：

| 现状 | 问题 | 改法 |
|---|---|---|
| `default_policy.py` 的启发式（失败率 >0.5 → 窄化 tool_set） | 硬编码量化语义阈值 | 改成可配置策略 + 从 Tier 2 读参数 |
| `golden_eval.py` 用 `evals.run_evals_llm._run_one` | 耦合仓库根的 `evals/` 包 | 抽成 `runner` 注入点（已有参数，只需把默认值改成「必须注入」） |
| `holdout.py` 的 `EventStoreReportSource` 读 `assignment.report` | 已迁到 L3，OK | — |
| `evolution` 作为 L2 包，但 kernel 通过 capability 解析它 | 方向正确（L2→L3 注册） | 保留 |

**`apps/evolution` → `kernel/evolution/`？** 不建议。理由：它仍然是「业务侧提供的进化策略」，
而且它依赖 `foundation.storage.holdout`（L4）+ `kernel.*`（L3）——放 L2 是正确的层位。
**判据是业务密度，不是「听起来像框架」**。

### 2.2 `templates/.prompts/` 分成两半

68 个提示词实测分类：

| 类别 | 数量 | 内容 | 处置 |
|---|---:|---|---|
| **通用**（框架该留） | 8 | `chat.md` · `orchestrator.md` · `planner.md` · `evaluator.md` · `workflow_orchestrator.md` · `_common/rules/{INDEX,tools,json-output}.md` · `_common/principles.md` | **留**，去研究化 |
| **业务**（研究域角色） | 24 | `researcher` · `factor_analyst` · `strategist` · `risk_controller` · `critic` · `anti_overfit_analyst` · `portfolio_construction` … | **归档** |
| **中性/边界** | 36 | `data_quality` · `position_auditor` · `report_writer` · `market_scanner` … | 逐个人工裁定 |

`.skills/` 26 个文件**全部是研究技能**（`factor-research` · `tushare` · `multi-factor` …）→ 归档。
但 `kernel/agent/harness_resolver._load_skills` 依赖这个目录——需要保留一个空目录 + 优雅降级
（已有 `try/except`，实测过）。

### 2.3 `builtin_tools/` 是 L3 里的 L2

5 个文件 / 3,074 行业务工具住在 `kernel/agent/builtin_tools/`。
`docs/design/agentnodes-split-plan-20261104.md` §3.2 已经点过这个（「6 个文件贡献 32/40 条上行」）。

**但 L3→L2 现在是 0**——因为这些工具用 `resolve()` 拿实现，不直接 import。
所以它们不是「违规」，是「**住错层**」。

处置：**移到 `apps/tools/`（L2）**，由 `apps/capabilities.py` 注册成 capability kind。
`kernel/agent/builtin_tools/__init__.py::build_default_registry` 改为从 registry 拉取。
这样 L3 的 `builtin_tools/` 只剩框架工具（shell / file / web / todo / subagent / bg / display / help / workspace）。

---

## 3. 新分批方案（按「删除量 ÷ 风险」排序）

### 批次 4 · 回测与验证域（最低风险，最大块）

**目标**：删掉整个「跑回测」能力，只保留 agent 框架。

| 删除对象 | 文件 | 行数 | 前置 |
|---|---:|---:|---|
| `apps/backtest.py` | 1 | 811 | — |
| `apps/backtest_engine/` | 5 | 498 | — |
| `apps/backtest_models.py` | 1 | 12 | — |
| `apps/run_card.py` | 1 | 242 | — |
| `apps/config_runner.py` | 1 | 605 | — |
| `apps/strategy_acceptance/` | 4 | 883 | — |
| `apps/engine/` | 21 | 2,350 | — |
| `apps/factor_validate.py` | 1 | 581 | — |
| **合计** | **35** | **5,982** | |

**L1 同步删**：
- `routers/` 无（回测没路由）
- `cli/__init__.py` 的 `run` / `evaluate` / `list` / `backtest` / `check` / `engine` / `accept` 子命令
- `cli/commands/merged.py`（`add_backtest_parser` / `add_check_parser`）
- `mcp/server.py` 的 `RunBacktestTool` / `ComputeFactorTool` 注册
- `apps/capabilities.py` 的 `backtest.*` / `config.runner` / `data.clean` kind

**kernel 侧降级**：`builtin_tools/{backtest,backtest_analysis,factor}_tools.py` 的 resolve
全部是惰性 + try/except → kind 消失后运行时降级。但**工具本身还在 registry 里**，
调用会报 `CapabilityNotFound`。应改为**不注册**（见批次 5）。

**风险点**：`apps/evolution/golden_eval.py` 的默认 runner 是 `evals.run_evals_llm._run_one`，
它**不依赖** `apps.backtest`。所以 RSI 闭环不受影响 —— 这是批次 3 勘查的结论。

**验收**：`evals/run_evals.py` 仍能跑（它调 LLM 而非回测）。

---

### 批次 5 · 数据域

| 删除对象 | 文件 | 行数 |
|---|---:|---:|
| `apps/data_source/` | 13 | 2,141 |
| `apps/data_import.py` | 1 | 496 |
| `apps/data_readiness.py` | 1 | 426 |
| `apps/db.py` | 1 | 1,273 |
| `apps/duckdb_store.py` | 1 | 407 |
| `apps/compute_factor.py` | 1 | 1,388 |
| `apps/tools/` | 4 | 822 |
| **合计** | **22** | **6,953** |

**L1 同步删**：`cli` 的 `import` / `validate` 子命令；`mcp/server.py` 的 `data_tools` 注册。

**kernel 侧**：`builtin_tools/{data,data_clean}_tools.py`（947 行）移出。

**风险点**：`apps/preflight.py` 依赖 `data_source` 与 `db` → 一起删。
`apps/workspace_setup.py`（168 行）生成 `.skills/` + `.prompts/` 脚手架 →
改成**只生成框架通用模板**（不生成研究技能）。

---

### 批次 6 · 业务工具出 L3（结构性，非删除）

| 动作 | 文件 | 行数 |
|---|---:|---:|
| `kernel/agent/builtin_tools/{factor,data,backtest,backtest_analysis,data_clean}_tools.py` → 删 | 5 | 3,074 |
| `kernel/agent/builtin_tools/goal_tools.py` → 删（goal 已不存在，纯死代码） | 1 | 582 |
| `build_default_registry` 改为从 `CapabilityRegistry` 拉业务工具 | 1 | ~50 改动 |

**这批次删 3,656 行 L3，且是「住错层」的清理，不是功能削减。**

**风险点**：`interfaces/mcp/server.py` 直接 import 这些工具类 → 需同步删。

---

### 批次 7 · `templates/` 去研究化（收尾）

| 动作 | 内容 |
|---|---|
| `.skills/` 26 个研究技能 | 归档 `testcase/golden/templates/` |
| `.prompts/` 24 个研究角色 | 归档 |
| `.prompts/_common/rules/backtest.md` | 归档（152 行业务规则） |
| 保留 8 个通用提示词 + `_common/principles.md` | 去研究化改写 |
| `templates/{config.yaml,prepare.py,program.md,strategy.py,README.md}` | 归档（研究模板） |
| `templates/workflows/*.json`（4 个） | 归档 |

**风险点**：`harness_resolver._load_skills` 读空目录 → 已有优雅降级，但要验证。
`tier0_snapshot._collect_methodology` 读 `_common/*.md` 的 sha256 → 保留 `_common/` 即可。

---

### 批次 8 · `apps/evolution` 去研究化（不是删除，是改造）

| 动作 | 说明 |
|---|---|
| `default_policy.py` 的阈值参数化 | 从 Tier 2 基因组读，不硬编码 |
| `golden_eval.py` 的 `runner` 改为必填 | 去掉对仓库根 `evals/` 的隐式依赖 |
| `evals/` 包整体移入 `apps/evolution/golden_tasks/` 或保留为可选插件 | 取决于是否随框架分发 |
| 重命名 `apps/evolution` → `apps/evolution`（不变，它已是框架机制） | — |

**这批不删代码，是「让 RSI 闭环能在无业务环境下自洽运行」的验证批次。**

---

### 批次 9 · 包名重命名（最后一步）

`strategy_research` → `agentnodes`：

- `src/strategy_research/` → `src/agentnodes/`
- `pyproject.toml` 的 `[project] name` + entry points
- 全部 import 改写（`scripts/migration/rewrite_import_prefix.py` 已有）
- 环境变量 `QUANTNODES_RESEARCH_*` / `SR_*` → 统一命名

**为什么最后做**：改名会让所有 git blame 断裂一次。等代码稳定了再改。

---

## 4. 优先级与依赖关系

```
批次 4（回测与验证，5,982 行）        ← 现在可做，风险最低
    ↓ 无硬依赖
批次 5（数据域，6,953 行）            ← 需先确认 preflight/workspace_setup 的处置
    ↓
批次 6（业务工具出 L3，3,656 行）      ← 依赖 4+5 完成（否则工具指向的 kind 还在）
    ↓
批次 7（templates 去研究化）          ← 依赖 4+5+6
    ↓
批次 8（evolution 去研究化）          ← 依赖 7（templates 定型后才知道 skills 从哪来）
    ↓
批次 9（包名重命名）                  ← 必须最后
```

**可并行**：批次 4 与「批次 8 的 `golden_eval` runner 注入化」互不影响，可同时做。

---

## 5. 每批次的固定动作（沉淀成流程）

来自批次 1–3 的实战教训：

1. **doc first** —— 先写 `docs/batchN-*-design-2026MMDD.md`，含依赖面实测表
2. **安全点** —— 若被删对象里含通用件，先单独 commit 提取（如批次 3 的 `c69e1fde`）
3. **门禁先跑** —— `check_dangling_imports` → 拿到精确清单，按清单处置
4. **批量归档** —— `git mv tests/<X> testcase/tests/`，不逐个手改
5. **跑收集** —— `--collect-only` 必须 0 errors（比全量回归快 50 倍）
6. **全量回归** —— 与上一批次的失败集**差分**，只关心新增
7. **架构图** —— `sync_architecture_diagram.py --write` + 版本史条目
8. **commit** —— 消息里写清「为什么删」而不只是「删了什么」

### 已知的「假失败」清单（不必修）

批次 3 后 49 个失败全部是既存问题，与减法无关：

| 类别 | 数量 | 例子 |
|---|---:|---|
| xdist 并行抖动（串行通过） | ~8 | `test_data_clean_tool` · `test_swarm_retry` |
| 环境缺依赖 | 5 文件 | `test_e2e_*` / `test_webui_*`（缺 playwright） |
| 既存缺陷 | ~40 | `test_sandbox` · `test_combo_tools` · `test_tool_retry` |

---

## 6. 本规划**不做**的事

| 项 | 理由 |
|---|---|
| 把 `apps/evolution` 删掉 | 它是用户目标的核心（自我进化 agent），业务密度 1.5% |
| 把 `apps/evolution` 挪进 kernel | 它依赖 L4 的 holdout 存储，L2 是正确层位 |
| 现在就做泛化「目标/准则/证据」机制 | 路线图 §5.1，等框架稳定后 |
| 现在就做 `graph_shape` Tier 1 组件 | 结构性变更，用户已决定延后 |
| 修那 40 个既存测试失败 | 与减法正交，单独排期 |

---

## 7. 终态预估

批次 4–9 完成后：

| 层 | 现在 | 终态预估 |
|---|---:|---:|
| L1 interfaces | 23,863 | ~14,000（删回测/数据/引擎 CLI + 路由） |
| L2 apps | **16,284** | **~2,000**（只剩 `evolution` 1,916 + `capabilities` 296） |
| L3 kernel | 37,699 | ~34,000（删 3,656 行业务工具） |
| L4 foundation | 10,925 | 10,925（不变） |
| **总计** | **88,771** | **~61,000（-31%）** |

从起点 144,086 行算起，**总减法 58%**。
