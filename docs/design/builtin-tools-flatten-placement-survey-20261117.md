# apps/builtin_tools 消包方案（已拍板）+ 全仓归属梳理盘点

> **日期**: 2026-11-17 · **状态**: 方案已拍板，**批次 α 已实施（同日，验证全绿，未提交）** · **决议**: ① 消包走方案 A；② 组 4 死代码候选**后续处理，当前忽略**；③ 本文档存档
> 后续会话可从本文直接恢复上下文。相关文档：`arch-diagram-l3-clusters-v2-20261117.md`（架构图草稿，NODES 已随批次 α 同步）。

## 1 · 现状与定性（调查结论）

- ~~`apps/builtin_tools` 包里只剩单文件模块 `goal_tools.py`~~ **已实施**：包已删除，`goal_tools.py` 平移至 `apps/goal_tools.py`（git mv 保留历史）。
- **调用权不存在错位**（ADR-003 端口化，批次 6 归位时已设计好）：
  - 框架工具本体在 L3 `kernel/agent/builtin_tools`（11 模块 3145 行），由组合根 `kernel/gateway/singleton.py:92` 调 `build_default_registry` 装配 —— kernel 调用 ✓
  - 业务工具在 L2 只提供**工厂**：`apps/capabilities.py` 注册为 `tool.goal` kind → kernel 侧 `_register_business_tools`（`kernel/agent/builtin_tools/__init__.py:51`）从 CapabilityRegistry 拉；`kernel/agent/capability/__init__.py:95` 经 sys.modules 读取 CAPABILITIES 表
  - `goal_tools.py` 与 `apps/goal` **零 import 耦合**（纯 `capability.resolve` 解耦，实测验证）
- 问题本质是**名字说谎**：`builtin_tools` 描述的是批次 6 时的 6 个模块；批次 4/5 删底层能力后，W1 自检证明 5 个模块"永远注册不上"，批次 1 收尾（11-06）直删 5 文件 3091 行（考古锚 `5ebc7a12`），只剩 goal_tools。

## 2 · 拍板决议

| # | 议题 | 决议 |
|---|---|---|
| 1 | 消包方案 | **方案 A**：`apps/builtin_tools/goal_tools.py` → `apps/goal_tools.py`（平文件，与 apps/ 下 capabilities.py / data_store_registry.py / workspace_setup.py 风格一致）。备选 B（并入 apps/goal/tools.py）否决——goal_tools 与 goal 域零耦合，放进去制造假内聚 |
| 2 | 组 4 死代码候选（apps/algo、agentnodes/scripts/backfill_agent_id.py）及 kernel/seq_generator | **后续处理，当前忽略**——只记录在 §4，不动 |
| 3 | 文档存档 | 本文件 |

## 3 · 批次 α 实施蓝图（✅ 已实施 2026-11-17，验证全绿）

**目标态（已达成）**：`apps/builtin_tools/` 包删除（git rm `__init__.py`），`goal_tools.py` git mv 平移为 `apps/goal_tools.py`，import 路径 `agentnodes.apps.builtin_tools.goal_tools` → `agentnodes.apps.goal_tools`。

**实测结果（与蓝图预测一致）**：
- 层矩阵逐格不变（L2 行 75/33/11），上行违规 0，`check_dangling_imports` L3→L2 = 0
- 原版图管线重跑：**35 包 / 140 边 / 1978 符号 / 0 上行违规**，MVP 三层自动重算（A 9 包 1078 符号），无缺 tier 警告
- v2 草稿 NODES/边/页眉页脚同步；实测修正手写估值（apps 5 文件 / 1214 行，非估算的 6/1220）
- 验证：pytest 78+37 = **115 passed**（test_goal_tools / test_tool_display_goal / test_builtin_tools_comprehensive / test_layer_matrix / test_architecture_gates / test_workflow_builtin / test_capability_smoke / test_tool_plugin_discovery / test_goal_continuation_e2e），2 skipped（既有 apps/study 归档跳过）
- 残留引用核查：仅剩历史考古锚（`git show <sha>:...` 指向历史提交的路径，**有意不改**——改了反而伪造历史）与本记录的新增注释

**层级影响 = 零**：goal_tools 平移前后都是 L2（`layer_of` 前缀匹配落到 `agentnodes.apps`=L2），`test_layer_matrix.py` 棘轮计数不变；唯一门禁改动是 `LAYER_MEMBERSHIP` 删 `agentnodes.apps.builtin_tools` 条目。

**触点清单（7 处）**：

1. `apps/capabilities.py:157` —— `_tool_goal` 工厂内 import 路径
2. `scripts/arch/layer_matrix.py:43` —— 删 `"agentnodes.apps.builtin_tools": "L2"` 条目（goal_tools 由 `agentnodes.apps` 前缀兜底）；同步看 `tests/test_layer_matrix.py` 棘轮基线是否需要更新
3. `tests/test_goal_tools.py` —— import（:11、:17）+ 6 处 `patch("agentnodes.apps.builtin_tools.goal_tools._get_store", ...)` 字符串
4. `tests/test_tool_display_goal.py:16` —— import
5. `tests/test_builtin_tools_comprehensive.py:44-49` —— 守卫测试改为：断言旧路径 `agentnodes.apps.builtin_tools.goal_tools` 与 `...data_clean_tools` 均 ModuleNotFoundError + 新路径 `agentnodes.apps.goal_tools` 可导入
6. 架构图数据：原版管线 `tmp/probe/mvp_tiers_data.py`（apps/builtin_tools 节点定义删除）+ v2 草稿 `docs/architecture-layered-l3-clusters.html` NODES（删 `apps/builtin_tools` 条目，`apps` 包 n 12→21、边 `apps/builtin_tools→kernel/agent 6` 改挂 `apps/goal_tools`；扫描后包数 36→35）
7. 无需动（已核实）：`scripts/arch/check_tool_refs.py:132/153`（只引 kernel 侧）、`scripts/arch/semantics_placement.yaml`（无提及）、`score_layer_attribution.py:48`（只引 kernel 侧 display_tools）

**实施后必跑验证**：

```
pytest tests/test_goal_tools.py tests/test_tool_display_goal.py tests/test_builtin_tools_comprehensive.py tests/test_layer_matrix.py tests/test_architecture_gates.py
python scripts/arch/check_capability_requires.py   # 按 CAPABILITIES 表内 import 做 AST 双向差集，路径自动跟随，需实跑确认
python scripts/arch/check_tool_refs.py
python scripts/arch/check_dangling_imports.py --layers
# 图数据对齐：重跑 tmp/probe/scan_arch.py → build_payload.py → gen_arch_html.py
```

## 4 · 全仓同类盘点（15 处核查点，2026-11-17 实测）

**组 1 · 单模块包装包（7 个）**

| 包 | 唯一模块 | 行数 | 建议 | 状态 |
|---|---|---|---|---|
| `apps/builtin_tools` | goal_tools.py | 593 | 平铺 | **批次 α（已拍板）** |
| `kernel/agent/capability` | registry.py | 531 | 保留（ADR-003 子系统，`__init__` 有逻辑，会长大） | 关闭 |
| `kernel/agent/validators` | claim_validator.py | 227 | 可平铺 `kernel/agent/claim_validator.py`，低优先 | 待议 |
| `evals/adapters` | swebench_to_golden.py | 73 | 可平铺，低优先 | 待议 |
| `foundation/storage/holdout` | store.py | 263 | 可平铺 `holdout.py`，低优先 | 待议 |
| `kernel/runtimes/presets` | fanout.py | 231 | 可平铺，低优先 | 待议 |
| `interfaces/cli/tui/workers` | workflow_worker.py | 227 | 保留（Textual workers 惯例目录，预期增多） | 关闭 |

**组 2 · 同名双语义（1 个）**：`kernel/agent/builtin_tools` vs `apps/builtin_tools` —— 方案 A 落地后自然消解；kernel 侧名字名副其实（框架内置工具、组合根装配），保留原名。

**组 3 · 数据伪装成包（1 待议 + 3 健康）**

- `apps/swarm_presets`：0 个 .py，`__init__` 仅提供 `PRESETS_DIR`/`preset_path` + 37 个 YAML。**非死代码**：3 个消费方（`apps/goal/workflow_config.py:29`、`interfaces/cli/commands/swarm.py:13`、`interfaces/mcp/server.py:23`，均 `from agentnodes.apps import swarm_presets`）。包身份只为路径 helper，**建议保留**（helper 挪 kernel/swarm 违背批次 8"kernel 不持业务内容"决定）。
- `apps/agent_plugins/quant_templates`、`foundation/llm/data/providers/*`、`templates/`：纯数据目录，健康不动。

**组 4 · 生产零引用死代码候选（3 个）—— 后续处理，当前忽略**

| 项 | 事实 | 备注 |
|---|---|---|
| `apps/algo/backtest.py`（326 行） | src 零引用，唯一消费 `tests/test_backtest_no_lookahead.py`（2 用例） | 11-16 自 kernel 迁入（"业务名词归业务层"理由成立，但生产本就死代码）。候选处置：删+测试改对拍档案 / 保留待量化域复活 / 移 tests fixtures |
| `agentnodes/scripts/backfill_agent_id.py`（104 行） | src 零引用；包内运维脚本 | 候选处置：移仓库根 scripts/ 或删 |
| `kernel/seq_generator.py` | src 零引用（仅 `tests/test_seq_generator.py`） | layer_matrix 已标"待退役（归 event_log 权威 seq）"，**有既定决议的执行待办**，非新决策 |

**组 5 · kernel 内聚债（1 个，已知）**：`kernel/session → kernel/agent` 13 条 import（compact / chat_loop / event_store / prompt_builder / text_guard）。R-014 下沉后新形态，同层合法但耦合真实（service.py 对压缩与 loop 构造的依赖）。已在 `arch-diagram-l3-clusters-v2-20261117.md` 记录，另行立项。

**组 6 · 门禁陈旧注册（2 个）**：`scripts/arch/layer_matrix.py` LAYER_MEMBERSHIP 中
- `agentnodes.templates`（L2）—— templates 是数据目录，**非可导入包**（无 __init__.py）
- `agentnodes.kernel.scheduled_research`（L3）—— 批次 8 已迁 apps（现 `apps/scheduled_research/`）

两者 src 零 import，删条目零行为风险；需同步看 `tests/test_layer_matrix.py` 棘轮基线。其余 40 条注册已逐一验存有效。

**总账**：近期动手 3 项（α 消包 / β 清 2 条陈旧注册 / γ 退役 seq_generator※已含在"后续处理"内）· 需拍板 2 项（组 4 前两条）· 保留/健康 10 处。※按决议 2，β/γ 均顺延，仅 α 排期。

---
*调查方法备注：全部结论来自只读 AST/import 实测（临时脚本复刻 `tmp/probe/scan_arch.py` 口径 + 全仓 grep 两种 import 形式：`from a.b.c import` 与 `from a.b import c`——后者是第一轮漏检 `swarm_presets` 消费方的根因，已修正）。*
