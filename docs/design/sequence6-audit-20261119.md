# 顺序⑥只读审计：OptionsPricingTool / hooks / seq_generator

> **性质**：只读审计结论（供裁决）· **日期**：2026-11-19
> **来源**：improve-codebase-architecture skill 后并行只读审计（全仓 grep + AST 意图核验）
> **纪律**：静态零引用 ≠ 可删——必须查消费者、产品承诺、动态入口、文档裁决。

## 0. 结论摘要

| 候选 | 判定 | 下版本删除批 | 需公告 |
|---|---|---|---|
| OptionsPricingTool | **保留**（落位问题，非删除） | ✗ | 仅落位迁移需确认（批次 E） |
| hooks（kernel/hooks 包） | **保留**（真实运行依赖） | ✗ | ✗ |
| seq_generator.py | **退役候选**（既定决议执行待办） | ✓（前置同步后） | ✓ 短公告 + R-014 方案 |

## 1. OptionsPricingTool — 保留

实体：`kernel/agent/builtin_tools/options_tools.py:21`（`class OptionsPricingTool`，name=`options_pricing`，Black-Scholes，依赖 scipy）。

**真实消费者**（排除静态提及）：
- `builtin_tools/__init__.py:34,112,199` — 默认注册表注册（`r.register(OptionsPricingTool())`）+ `__all__` export 面
- `apps/swarm_presets/{derivatives_strategy_desk,earnings_research_desk,convertible_bond_team}.yaml` — 6 处工具白名单条目
- `webui/frontend/src/components/chat/ToolCallBlock.tsx:61` — `options_pricing: Calculator` 图标映射（前端可见）
- 测试：`test_builtin_tools_comprehensive.py:20-28,194`、`test_strict_schema.py:173-176`

**产品承诺**：`docs/guides/agent-tools-reference.md:690-714` 独立条目；`templates/.skills/options-payoff.md` 配套 skill。

**判定依据**：这是架构文档 A6 / 批次 E 的「落位问题」（kernel→apps capability 注入），不是删除问题。`project-architecture-review...:125-129` 明确「根据真实产品承诺决定，而不是只按目录命名判断」。

## 2. hooks — 保留

实体：`kernel/hooks/{composite,context,utils,__init__}.py`（4 文件 195 行，已内聚）。

**真实消费者**：
- `kernel/agent/loop/core.py:44-45,336,455,595-599` — AgentLoop 构造参数 + run 循环全程 fire 6 类 hook 事件
- `kernel/agent/telemetry/agent_telemetry.py:208-235`、`kernel/agent/loop/telemetry.py:28-58`、`engine_adapters.py:208-333` — hook 适配与转发
- 测试：`test_hooks.py`、`test_hooks_and_fetch.py`、`test_p2_e2e.py` 等

**动态发现**：无 entry_points / 目录扫描用于 hooks；只有工具插件发现（group=`agentnodes.tools`），与 hooks 无关。

**产品承诺**：`docs/guides/extension-points.md:43-54` — 公开的「§1.2 观察 — Hooks」扩展点（11 个生命周期点 + 红线「hook 不得改变控制流」）。

**判定依据**：真实运行依赖 + 对外文档承诺。架构文档 `:472,564` 明确「若无其他 kernel runtime 消费者可考虑归入 agent/hooks，但不值得单独制造迁移批次」——有消费者，保持。

**需区分的另三套 hook**（不属本候选）：`SwarmHook`（swarm 生命周期）、`GoalWorkflowHook`、`LoopHooks`（引擎适配器协议）——各自独立。

## 3. seq_generator.py — 退役候选

实体：`kernel/seq_generator.py:33`（`SeqGenerator.next/peek/reset`）、`:85`（`get_default_generator()`）。

**生产消费者：0**。仓库内只有测试真实 import（`test_seq_generator.py:8`）。

**关键证伪**：docstring `:79-80` 称「The webui/server imports this directly」——全仓搜 `webui`/`examples` 对 `get_default_generator|SeqGenerator|seq_generator` 命中 **0**，该外部消费者声明不成立。

**产品承诺**：全部指向「待退役」而非「保留」：
- `scripts/arch/layer_matrix.py:66` — `L3, # 待退役（归 event_log 权威 seq）`
- `docs/architecture-diagram.html:249` — 同款标注
- `builtin-tools-flatten-placement-survey-20261117.md:83` — 「有既定决议的执行待办，非新决策」
- 多份架构文档一致（split-plan / target-directory / e1-replaceability / cleanup-plan）

**删除前置 4 触点**：
1. 修正 `web_session.py:114-116` / `kernel/session/models.py:117` 的陈旧 SeqGenerator 注释（实际回退列默认值，未调用生成器）
2. 删除或归档 `tests/test_seq_generator.py`（含 baseline 节点 ID 清理）
3. 删除 `layer_matrix.py:66` 注册 + 同步 `test_layer_matrix.py` 棘轮基线
4. 同步 `architecture-diagram.html:249`

**判定依据**：不是靠静态零引用删，而是靠「既定退役决议 + 文档一致 + docstring 伪消费者已证伪」三重证据。仍按 R-014 出方案 + 短公告确认无仓库外调用者后进删除批。

## 4. 顺带发现的陈旧文档（建议修复）

- `docs/guides/extension-points.md:54` — 提到 `hooks/unified.py` + `hooks/adapter.py` 为 DELETE-CANDIDATE，但两文件当前已不存在（陈述过时）。
- `examples/session_example.py:6` — `from agentnodes.core.hooks import ...`（旧 `core` 包名，示例已失效）。

## 5. 执行建议

1. **seq_generator**：列入下版本删除批（步骤 6 公告时一并公告），先做 4 触点同步 diff，R-014 方案先行。
2. **OptionsPricingTool / hooks**：从「删除候选」清单移除，重新分类为落位裁决（批次 E）/ 保持。
3. **陈旧文档**：两处顺带修复，不建独立批次。

（只读审计，未修改任何文件。）
