# 切分面实测（2026-11-04 · W12 阶段 1.5）

> 方法：`graphify extract . --code-only` 建全库图（**46,008 节点 / 87,970 边**，
> commit `93895651`），再用 AST 逐条复核图谱的高风险边。
> 本文所有数字**独立复核过**，与 `docs/agentnodes-split-plan-20261104.md` 不一致处
> 逐条标注。图产物在 `graphify-out/`（未跟踪，可重生成）。

---

## 0. 复核结论：规划文档三处需要修正

| # | 规划文档原话 | 实测 | 影响 |
|---|---|---|---|
| 1 | 「`kernel/agent` 里只有 **10 个文件**引用 apps」 | **13 个文件**（`builtin_tools/` 6 + `context_injector` + `dag_config` + `exec_registry` + `loop` + `scheduled_research/` 2 + `swarm/runtime`） | 阶段 3 工作量 +30%；且漏了 `scheduled_research` 与 `swarm` |
| 2 | `foundation/utils/` 10 个文件「需随业务走」 | **确实如此，且更强**：这 10 个文件在 kernel 侧**零消费**（图证实 in-edge from kernel = 0） | 无争议，直接随业务删 |
| 3 | `templates/` 是「策略模板是业务内容」 | **不成立**。`templates/.prompts/*.md` 是 agent 框架的 role prompt 唯一来源（`role_factory` 14 角色 + `PromptBuilder`）；`templates/workflows/*.json` 是 `kernel/workflow/builtin` 的内置定义 | **不能整删**；已确认切分方案见 §4 |

另：图中 `options_tools.py` / `runtimes/nodes/react.py` / `runtimes/presets/fanout.py`
三处「→ apps」边经 AST 复核为**误报**（`sqrt`/`log` 与 `registry()` 同名模糊匹配）。
真实 kernel→apps 是 13 个文件，不是 16 个。

---

## 1. 图形状：两侧质量完全不对称

```
区域         节点    内部边    占比
apps       13,683   15,893    ← 被切掉的一侧
kernel      3,383    6,092
foundation    959    1,522
interfaces  2,198    4,045
tests      21,631   27,371
templates      71       83
webui       1,389    3,658
```

**关键方向性结论**：

| 方向 | 边数 | 判定 |
|---|---:|---|
| `foundation → apps` | **0** | ✅ 完全干净，L4 从未上返业务 |
| `kernel → apps` | **113** | ⚠ 13 个文件，全部 lazy import（除 2 处 builtin_tools 模块级） |
| `apps → kernel` | **96** | ✅ 预期方向（业务依赖框架） |
| `interfaces → apps` | **439** | 随接口层一起删，无需处理 |
| `tests → apps` | **4,445** | 按 §3 分类处理 |

> `foundation → apps = 0` 是本拆分最有利的事实：**底层不需要任何解耦**。

---

## 2. kernel → apps 的真实接缝（阶段 3 的工作清单）

13 个文件、113 条边。按解耦手段分组：

### 2.1 纯业务工具 → 整体移出（6 个文件，占 32 条边的主体）

| 文件 | LOC | 消费的 apps 符号 |
|---|---:|---|
| `builtin_tools/data_tools.py` | 758 | `db.{get_connection,init_db,save_ohlcv_to_db}`、`data_source.{resolve_loader,NoAvailableSourceError,_ensure_registered,validate_date_range,detect_market}`、`data_readiness.check_data_readiness`、`config_runner.load_yaml_config` |
| `builtin_tools/factor_tools.py` | 1150 | `compute_factor.{compute_factor,FactorComputeError}`、`db.get_connection`、`tools.data_transforms.long_to_single_asset_wide` |
| `builtin_tools/backtest_tools.py` | 510 | `backtest.run_backtest_from_yaml`、`config_runner.{load_yaml_config,load_data}`、`data_readiness.check_data_readiness` |
| `builtin_tools/backtest_analysis_tools.py` | 448 | `db.get_connection` |
| `builtin_tools/data_clean_tools.py` | 168 | `db.{get_connection,save_ohlcv_to_db}`、`tools.data_clean.clean_data` |
| `builtin_tools/goal_tools.py` | 574 | `goal.{GoalStore,models.EvidenceInput,models.GoalStatus,context.default_goal_criteria}` |

**手段**：`build_default_registry()` 已经是「try + 条件注册」结构
（`__init__.py:80-124`），把这 6 个域改为**入口点注册**即可——框架侧只剩空壳，
业务侧（quantnodes）用 `[project.entry-points."...tools"]` 注回。
`_discover_tool_plugins()`（`__init__.py:137`）已经实现了这套机制，**零新增基础设施**。

### 2.2 通用能力但绑了业务类型（4 个文件）

| 文件 | 消费 | 解耦手段 |
|---|---|---|
| `agent/context_injector.py` | `goal.context.{get_current_goal_context,goal_needs_continuation,format_goal_continuation_prompt}`、`hypothesis.registry.HypothesisRegistry` | 已是 try/except + 开关（`enable_goal_injection`）。改为**注入槽**：`loop.goal_context_provider` 回调，业务侧注册 |
| `agent/loop.py` | `goal.store.GoalStore`、`hypothesis.auto_create.HypothesisAutoCreator` | 同上（已有 `enable_hypothesis_auto_create` 开关） |
| `agent/exec_registry.py` | `backtest.run_backtest_script`、`strategy_acceptance.decide` | 已是 try/except ImportError。改为 executor **注册表**，业务侧注册 |
| `agent/dag_config.py` | `study.graph.{StudyGraph,GraphNode,GraphEdge}` | `to_study_graph()` 独立方法，调用时才需要 → 移出到业务侧 |

### 2.3 整包属于业务域（2 个包）

| 包 | LOC | 消费 | 判定 |
|---|---:|---|---|
| `kernel/scheduled_research/` | 1,194 | `study.{StudyStore,StudyScheduler,StudyStatus,bootstrap.create_study_record}` | **整个包移出**——它是 study 域的调度器，不是通用调度 |
| `kernel/swarm/runtime.py` | — | `backtest.run_backtest_script`、`strategy_acceptance.decide`、`goal.expression_evaluator.evaluate_condition` | 3 处 lazy import，改为能力注入 |

### 2.4 eager 依赖只有 2 处（关键安全事实）

全库 `apps` 的模块级（非函数内、非 try）import 仅 **5 处**：

```
interfaces/api/routers/study_helpers.py:11      ← 随接口层删
interfaces/cli/tui/widgets/dag_view.py:32        ← 随接口层删
scripts/migrate_study_history.py:31              ← 一次性脚本，删
kernel/agent/builtin_tools/backtest_tools.py:10  ← §2.1 移出
kernel/agent/builtin_tools/factor_tools.py:8     ← §2.1 移出
```

**含义**：kernel 侧 209 处 apps 引用里 **203 处是 lazy 的**。删掉 `apps/`
不会造成 `ImportError` 雪崩，只会让相关功能在调用时降级——这让减法可以分小步验证。

---

## 3. 测试归属（780 个测试文件）

按「导入 apps / 只导入 kernel+foundation+interfaces」分类：

| 类别 | 数量 | 处置 |
|---|---:|---|
| **business-only**（只导入 apps） | **170** | 随 apps 一起删 |
| **agent-only**（不碰 apps） | **435** | 全保留 |
| **mixed**（两者都导） | **78** | **逐个人工裁定**，不能批量删 |

mixed 里最需要小心的：

- `test_all_tools.py`（kernel 118 / apps 4）——**保留**，删掉 4 条业务工具断言
- `test_agent_tools.py`（kernel 128 / apps 1）——保留，删 1 条
- `test_tools_integration.py`（kernel 55 / apps 3）——保留，删 3 条
- `test_final_band_push.py`（kernel 25 / apps 21）——接近对半，需逐条看
- `test_layer_matrix.py`——**门禁本体**，需重写（去掉 L2 成员表与 apps 相关基线）
- `test_v05_unit_checkpoint_events_dag.py`（apps 102 / kernel 35）——偏业务，倾向删

> `tests/attic/` 下 3 个是历史残留（不被收集），直接忽略。

---

## 4. `templates/` 的正确切法（修正规划文档）

框架侧真正依赖的，**只有 `.prompts/` 与 `workflows/`**：

| 子目录 | 内容 | 消费方 | 归属 |
|---|---|---|---|
| `.prompts/` | 14 个 role prompt + `_common/principles.md` | `role_factory`、`prompt_builder`、`prompt_schema_extractor` | **框架保留** |
| `workflows/*.json` | 4 个内置工作流定义 | `kernel/workflow/builtin.py:20` | **框架保留**（但 `alpha_research.json`/`data_quality_audit.json` 是业务内容） |
| `.skills/` | 26 个量化技能包（alpha-zoo、tushare、multi-factor…） | `workspace_tools`、`cli/commands/skills.py`、`mcp/server.py` | **内容属业务**；加载机制属框架。当前 plan 结论：先留作测试数据 |
| `strategy.py` / `prepare.py` / `config.yaml` / `program.md` | 策略模板 | `apps/study/bootstrap.py`、`apps/workspace_setup.py` | **业务，随 apps 删** |

**用户决策（本轮）**：`templates/` 先保留，`.skills` 等内容后续作为测试数据使用。

---

## 5. `foundation/utils/` 的 10 个业务文件（图证实零争议）

图数据显示这些文件 **kernel 侧 in-edge = 0**，只有 apps / tests / 互相引用：

| 文件 | LOC | kernel 消费 | apps 消费 |
|---|---:|---:|---:|
| `backtest_models.py` | 86 | **0** | 19 |
| `backtest_config.py` | 81 | **0** | 17 |
| `backtest_utils.py` | 182 | **0** | 11 |
| `backtest_engine.py` | 190 | **0** | 9 |
| `market_detection.py` | 97 | **0** | 8 |
| `strategy_engine.py` | 194 | **0** | 7 |
| `metrics.py` | 241 | **0** | 3 |
| `backtest_metrics.py` | 314 | **0** | 2 |
| `covariance.py` | 92 | **0** | **0** ← 已死 |
| `ic_utils.py` | 67 | **0** | **0** ← 仅测试引用 |

**判定**：前 8 个随 apps 走；`covariance.py` 与 `ic_utils.py` **连业务侧都不用了**，
属既存死代码（`ic_utils` 只有 `test_ic_utils.py` 在测），建议**直接删除**。

框架侧真正在用的只有 4 个：`bg_proc.py`（kernel 28）、`token_utils.py`（kernel 19）、
`ids.py`（kernel 4）、`io_utils.py`（iface 7）。

---

## 6. 阶段 2 的执行顺序（按风险递增）

| 步 | 动作 | 规模 | 风险 | 验证 |
|---|---|---:|---|---|
| **2a** | 删 170 个 business-only 测试 | 170 文件 | 极低 | 收集期不再报 import 错 |
| **2b** | 删 `foundation/utils/` 10 个业务文件 + `test_ic_utils` 等 | 12 文件 | 极低（kernel 零消费） | `check_dangling_imports` |
| **2c** | 删业务路由/命令（`study`/`goal`/`hypothesis`/`validation`/`slash_commands`/`autoresearch`/`export`） | 9 文件 | 低 | 接口层 import 冒烟 |
| **2d** | 删 `interfaces/cli/__init__.py` 里的业务子命令注册 | 1 文件大量行 | 中 | CLI `--help` |
| **2e** | 删 `apps/` 整个包（609 文件） | 609 文件 | 中 | kernel import + 门禁 |
| **2f** | 修 78 个 mixed 测试 | 78 文件 | 中 | 全量测试 |
| **2g** | 改 `kernel` 13 个接缝（阶段 3） | 13 文件 | 高 | 全量测试 |

**2a–2e 不触碰任何 kernel 代码**，是纯删除；**2f–2g 才需要改代码**。
建议 2a–2e 一次提交（可整体 revert），2f 单独提交，2g 按包分批。

---

## 7. 顺手发现的真实缺陷（3 处）

### 7.1 `prompt_builder._discover_prompts()` 路径少一级（真 bug · 已修）

```python
# kernel/agent/prompt_builder.py:364（修复前）
prompts_dir = pathlib.Path(__file__).parent.parent / "templates" / ".prompts"
#                             ^ 文件在 kernel/agent/，parent.parent = kernel/
#                             → kernel/templates/.prompts  ← 不存在
```

**后果**：`PromptBuilderFactory.get(<未硬编码角色>)` 永远返回 `_NullBuilder`
→ 该角色拿到空 system prompt。14 个硬编码角色不受影响，所以一直没暴露。

**已修**：改用同文件 line 50 的模块级常量 `_PROMPTS_DIR`（那条路径本来就是对的）。
验证：`test_prompt_auto_discovery{,_extended}.py` + `test_dsh_comprehensive.py` 共 62 项全绿。

> 为什么测试没抓到：`test_discover_prompts_returns_dict` 只断言返回 `dict`，
> `test_discover_skips_hardcoded_roles` 只断言"不含硬编码角色"——
> **空 dict 同时满足这两条**。缺的是「非空」断言。

### 7.2 `covariance.py` / `ic_utils.py` / `risk_parity.py` 无生产消费者（已核实）

见 §5。import 语句级复核：

| 文件 | 生产 import | 仅测试引用 |
|---|---|---|
| `covariance.py` | 0 | `test_covariance_ic.py:22` |
| `ic_utils.py` | 0 | `test_covariance_ic.py:22`、`test_ic_utils.py:14` |
| `risk_parity.py` | 0 | `test_risk_parity.py:13` |

> `risk_parity` 有 40 处文本命中，但全部指向 `apps/portfolio/combiner.py` 的
> **同名独立函数**，与 `foundation/utils/risk_parity.py` 无关。

### 7.3 【重要】CI 从未真正运行过 async 测试

`pyproject.toml` 配了 `asyncio_mode = "auto"`，但：

- `[project.optional-dependencies].dev` 里**没有 `pytest-asyncio`**
- CI 装的是 `pip install -e ".[dev,api]"`（`tests.yml:20,50`）

实测后果：

```
PytestConfigWarning: Unknown config option: asyncio_mode
PytestUnknownMarkWarning: Unknown pytest.mark.asyncio - is this a typo?
async def functions are not natively supported.
```

即 **async 测试以 `Failed: async def functions are not natively supported` 失败**，
而纯 async 用例在无插件时是被**跳过/报错**而非执行。同理 `timeout = 180`
也因为缺 `pytest-timeout` 而**完全没生效**——pyproject 里写的那个
「global backstop so one pathological test can never hang a whole run」
（`pyproject.toml:95-99`）**实际上不存在**。

**已补装**：`pytest-asyncio` + `pytest-timeout` + `pytest-xdist`。
**待办**：把这两个包写进 `[project.optional-dependencies].dev`，
否则本地/CI 装出来的环境仍缺。

> 这条直接影响本次减法的安全网：不修的话，"跑了全量测试"这个结论是假的。

---

## 附：复现方式

```bash
graphify extract . --code-only     # 重建图，约 40s，产出 graphify-out/graph.json
python scripts/arch/check_dangling_imports.py
python scripts/arch/check_path_literals.py
```

本文分析脚本（临时，未入库）：
`g_split.py`（区域边统计）· `g_seam.py`（apps 消费面）· `g_kernel.py`（kernel→apps 接缝）
· `g_tests.py`（测试分类）· `g_utils.py`（foundation/utils 消费面）· `g_verify.py`（误报复核）
