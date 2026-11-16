# 项目进度评估（复盘）

> **性质**：复盘 / 进度评估 · **日期**：2026-11-16 · **基线**：`ui-workbench` @ `a15280b0`（= `origin/ui-workbench`，工作区干净，未推送 0）
> **口径**：本文件所有数字均为本次实跑所得（pytest / batch_gate / 层级门禁 / vitest coverage / git 溯源 / 自建 probe 脚本），**非文档转述**。
> **复核网**：`tmp/probe/doc_verify.py`（45 条事实断言）+ `tmp/probe/doc_paths2.py`（64 条路径/行号引用）= **109 条**，全部通过（唯一 "BAD" 是刻意反向检验「pytest 无 nouns 用例」）。
> **本报告推翻了自身的两条初步判断**：`kernel/algo` 可直接删（→§4.4 实测推翻）、层编号按 rules.md（→§1.1 实测推翻）。详见附录 C。
> **性质边界**：本文件只评估、不改代码；末尾 §6 的处置方案**待批准后另行实施**。

---

## §0 结论摘要

**一句话**：代码库处于「主干收敛完成、新功能零开工」的状态。测试与门禁体系总体健康（**一处红线**），文档密度远超代码产出速度；`ui-workbench` 分支 533 个 commit 中绝大多数是**减法 / 重构 / 测试 / 文档**，而非新能力。

| 维度 | 状态 | 实测 |
|---|---|---|
| 后端代码 | 稳 | `src/agentnodes` 412 文件 / 90,084 行；四层 L1 12,077 / L2 41,342 / L3 9,328 / L4 27,337 |
| 后端测试 | 强 | `tests/` 528 文件 / 119,265 行；**7302 passed / 115 skipped / 4 xfailed / 0 failed**（125.59s，`-n 8 --dist loadgroup`）|
| 前端代码 | 中 | `webui/frontend/src` 297 文件 / 52,087 行 |
| 前端测试 | 强 | **122 文件 / 1323 用例全绿**，总覆盖 **89.60% stmt / 81.85% branch / 79.12% func / 89.60% line**（85% 目标达成）|
| 架构门禁 | 🔴 **一处红** | `batch_gate` FAIL —— `check_kernel_nouns` **51 处**业务名词命中 |
| 文档 | 重 | `docs/` 389 份 md（design 104 · archive 266 · guides 13 · root 5 · checkpoints 1）/ 84,675 行 |

三条最需要决策的事实：

1. **UI 工作台五道工序（U-A→U-E）代码零开工**。本分支的 UI 实际产出 = 单元测试轨（T1–T4，已收口）+ 6 份调研/设计文档；涉及样式 / 令牌的 commit 数 = **0**。
2. **🔴 `batch_gate` 当前是红的**，且该红线**pytest 不覆盖、CI 不跑** —— 两道防线同时缺失。这是一处**此前未被任何文档记录**的缺陷。
3. **🔴 `kernel/algo` 触发的门禁红线不能靠「删掉」解决**。它承载着全仓**唯一**的无前视修复实现，而仓库里两份 tracked 的 `prepare.py` 副本**实测仍带前视 bug**（§4.4）。初次评估的「死代码→直接删」判断**已被实测推翻**。

---

## §1 规模实测

### 1.1 后端分层（`src/agentnodes`）

> ⚠️ **层编号口径**：本仓有**两套互相矛盾**的编号。门禁权威口径（`scripts/arch/layer_matrix.py:5`，自称「唯一事实源」）= **L1 interfaces / L2 apps / L3 kernel / L4 foundation**；但 `.rules/rules.md` §1.1 写的是「foundation（L1）← kernel（L2）← apps（L3）← interfaces（L4）」——**方向完全相反**。
> 实测判定：以门禁输出矩阵反推（L4 行仅对角 97 → L4 不 import 任何人 → L4 = 最底层 = foundation；L1 行 import 全栈 → L1 = interfaces）→ **门禁口径正确，rules.md §1.1 的编号是错的**。
> **本报告下文一律采用门禁口径**（L1=interfaces / L2=apps / L3=kernel / L4=foundation）。凡出现「L3→L2」均指此口径。

| 层（门禁口径）| 目录 | 文件 | 行数 | 判读 |
|---|---|---:|---:|---|
| L4 | `foundation/` | 63 | 12,077 | 零业务依赖基础设施（最底层）|
| L3 | `kernel/` | 191 | 41,342 | 框架内核（含 `algo/` 新增 2 文件 / 316 行，见 §4.3）|
| L2 | `apps/` | 40 | 9,328 | 业务能力（`apps/goal/` 占 18 文件 / 5,975 行）|
| L1 | `interfaces/` | 118 | 27,337 | 交付界面（最上层）|
| — | 合计 | **412** | **90,084** | |

层级方向实测**合规**：`check_dangling_imports.py --layers` → 上行违规 6 项全为 0、**L3→L2 = 0**（即 kernel→apps = 0，核心不依赖业务）、悬空 = 0。

### 1.2 测试与工具

| 资产 | 规模 |
|---|---|
| `tests/`（后端）| 528 文件 / 119,265 行 |
| `webui/frontend/src/test/` | 122 文件 / 18,017 行 |
| `scripts/` | 89 文件 / 11,401 行 |
| pytest 收集数 | 7421（锚点下限 7178，0 errors）|
| 前端覆盖率 | api 99.72% · pages 96.21% · stores 93.69% · utils 99.17% · components 85.10% |

### 1.3 复杂度热点（`src/` 前 10）

| 行数 | 文件 |
|---:|---|
| 1809 | `apps/goal/store.py` |
| 1581 | `kernel/agent/loop.py` ← **目标 ≤650，未达** |
| 1468 | `interfaces/api/session/service.py` |
| 1090 | `kernel/evolution/validation_gate.py` |
| 1043 | `interfaces/api/routers/chat.py` |
| 1007 | `kernel/agent/tools.py` |
| 1005 | `interfaces/cli/onboard.py` |
| 1003 | `apps/goal/workflow.py` |
| 943 | `foundation/settings.py` |
| 888 | `interfaces/cli/commands/slash_goal.py` |

### 1.4 Git 状态

| 项 | 值 |
|---|---|
| 当前分支 | `ui-workbench` @ `a15280b0` |
| 未推送 commit | **0**（`origin/ui-workbench` 与 HEAD 同步）|
| vs `main` | main 独有 0 / ui-workbench 独有 **533** |
| vs `u6-2-terminal-cleanup` | u6-2 独有 **5** / ui-workbench 独有 29（`git cherry` 全 `+`，无 patch-equivalent）|
| 工作区 | clean |

> ⚠️ **文档漂移**：`docs/INDEX.md` §2 标题写「design/ — 仍生效的设计与契约（**50 份**）」、§3 标题写「archive/（**264 份**）」，实测 `docs/design/` **104** 份 md（含 `adr/` 3 份）、`docs/archive/` **266** 份。索引计数未随本轮多份新文档同步。

---

## §2 四条工作线完成度

### 2.1 UI 工作台（U-A → U-E）：**设计完备，代码 0 开工**

| 工序 | 状态 | 关键证据 |
|---|---|---|
| **U-A** 令牌体系 | 仅设计 | `webui/frontend/src/styles/globals.css` = 653 行 / 106 处定义 / 55 个去重变量，最后提交 `bc2c2376`（**早于本分支**）；`src/styles/` 下**只有** `globals.css`；`src/theme/`、`src/tokens/` 均不存在；全仓无 `tokens.css` / `design-tokens.*`；Tailwind 仍 `^3.4.19`，`@tailwindcss/vite ^4.3.3` 是**死依赖**（`vite.config.ts` 只挂 `react()`，`postcss.config.js` 仍在）|
| **U-B** 审批面 | 底座已通（**真数据**），但**非本线产物** | `components/chat/PermissionRequestDialog.tsx` 四档审批（allow-once/always、deny-once/always）→ `hooks/sse/permissionHandlers.ts:38-54` → `api/client.ts` → `POST /api/chat/permission/respond` → `routers/permission.py:31`；`git log` 溯源到 `d8711253`，早于本分支 |
| **U-C** 运行中任务 | 仅设计 | `components/workflow/DefinitionWorkflowPage.tsx` 有真 SSE（`/api/goal/workflow/run/{runId}/events`），但同为分支前既有功能；Trace/RSI 两面按 §6.7 明确**排除出本轮** |
| **U-D** 会话日志投影 | **被后端阻塞** | kernel **无 checkpoint / fork / steer API**；前端「会话日志」视图零命中；仅 `components/chat/TraceViewer.tsx`（REST 拉取 `GET /api/chat/session/{id}/trace`）|
| **U-E** 生成式 UI | **主动排除** | `gen-ui` / `genui` / `componentSpec` 全仓零命中；MCP-App 式宿主协议（`ui/initialize` / `ui/message` / display-mode）不存在；现有 iframe 载体（`HtmlBlock.tsx`、`PanelRenderCard.tsx`）是固定 kind 开关 + 硬编码 `bg-white` |

> **判定依据**：`ui-workbench-design-20261115.md:110`「实施未开始（用户明确：确认完毕后才开工）」；`:320`「6.1 转向确认仍是总前置」。本分支 `git log` 中涉及样式 / 令牌的 commit 数 = **0**。

### 2.2 后端清理 / 减法路线：**主体完成，尾部四项未收**

| 批次 | 状态 | 证据 |
|---|---|---|
| 新架构批次 1–4d / 6–12 | ✅ 完成 | `check_tool_refs` exit 0 · `check_capability_requires` consistent · `check_semantics_placement` consistent |
| 批次 13 S3 | ⚠️ 部分 | tools/progress/stream 三 mixin 仍在 MRO（`loop.py:272-274` 等 12 处 import）；`loop.py` 1,581 行（目标 ≤650）|
| 批次 5（R3 + W5）| ❌ 未做 | R3 preset 化未做；W5 假基因清理未做（`genome_version.py` 的 `deactivate()` 生产零调用方）|
| 旧批次 7（templates 去研究化）| ❌ 未做 | `.prompts` **17 份** + `.skills` **26 份**量化资产仍在；已决议延期到第三步，第三步未启动 |
| phase2（web_session/projector 分离）| ✅ 完成 | `interfaces/api/session/` 下 5 个投影模块齐备 |
| phase3（双工厂收口）| ✅ 完成 | `kernel/agent/loop_assembly.py::assemble_loop_parts` 被 3 处引用 |
| phase5（护栏 v1 observation）| ✅ 完成 | `kernel/agent/guardrails.py` + `kernel/hooks/composite.py` 挂点 |
| **phase1** | ⚠️ **D2/D3 达成，D1 未达成** | D2 权限 L0–L4 ✅ · D3 删 apps 仍通用 ✅ · **D1 ❌**：`kernel/agent/builtin_tools/file_tools.py` 只有 execute 系工具，**无 edit 工具** → C6 编辑唯一性 / C7 读后写保护**物理上不存在** |

### 2.3 B1 节点产物契约：**机制已落地，首个真实用例未挂**

- 已落地：`kernel/workflow/contract.py` 317 行 + `tests/test_workflow_output_contract.py` 33 用例 + `node_types.py:195` 单点接入（commit `a15280b0`）。
- **但全仓非测试代码中的契约声明数 = 0** —— 校验从未真正生效过。
- **验收门 5 未完成**，且字段漂移已定位（三名并存）：

| 位置 | 用的名字 |
|---|---|
| `apps/agent_plugins/quant_validators.py:69,72,74` | `max_drawdown` |
| `kernel/algo/backtest.py:139,250` | `max_dd` |
| `apps/goal/workflow_hook.py`（metric_keys）| `max_dd` |
| `templates/.prompts/risk_controller.md:70` | `max_drawdown` |
| `kernel/swarm/runtime.py:743`（分支表达式）| `risk_controller.output.max_drawdown` |

### 2.4 goal-capability-merge（R1/R2/R3）：**R1/R2 完成，R3 未动**

| 阶段 | 状态 | 证据 |
|---|---|---|
| R1（恢复 `apps/goal/`）| ✅ | 18 py / 5,975 行；`apps/capabilities.py:216-224` 注册 `goal.store` / `goal.models` / `goal.context`；`interfaces/api/routers/goal.py` + `interfaces/cli/commands/slash_goal.py`（888 行）接线，40+ 调用点 |
| R2（chat 接线）| ✅ | API 路由、TUI `dag_view` / `workflow_worker`、`apps/goal/events.py` 均在位 |
| R3（preset 化 + RSI 证据源）| ❌ | `apps/goal/journal_source.py`、`scoreboard_source.py` **缺失**；`interfaces/rsi_wiring.py:219` 四个槽位**仍同指一个 `EventStoreReportSource`**（`journal=source, scoreboard=source, validation=source, holdout=source`）；`build_autoresearch_workflow_config()` **零调用方**，其 8 个工具引用（`compute_factor` / `factor_analysis` / `get_market_data` / `run_backtest` / `git_diff` / `search_symbol` / `websearch` / `webfetch`）全悬空，被 `check_tool_refs.py:88` 显式豁免（理由写「R3 preset 化时整体删除」，而 R3 从未执行）；`kernel/evolution/holdout.py:347` 仍含死 kind `study.round_done` |

---

## §3 健康度实测

### 3.1 全绿项

| 项 | 命令 | 结果 |
|---|---|---|
| 后端全量 | `pytest tests -q -p no:hypothesispytest -p no:hypothesis -n 8 --dist loadgroup --timeout=600` | **7302 passed / 115 skipped / 4 xfailed / 0 failed**，125.59s |
| 层级方向 | `check_dangling_imports.py --layers` | 上行违规 0 · L3→L2 = 0 · 悬空 0 |
| 架构图同步 | 全量内含 `test_scripts_arch.py` | 23 passed（`test_architecture_diagram_matches_code` 在内）|
| 前端套件 | `vitest run --coverage` | **122 文件 / 1323 用例全绿**，89.60% |
| 其他门禁 | batch_gate 步骤 1–6、8 | 全 OK（collect 7421 / 悬空 0 / path 0 / capability consistent / semantic consistent / tool refs consistent / env access OK）|

### 3.2 🔴 红线项：`batch_gate` FAIL

```
[7/9] kernel nouns
  [X] check_kernel_nouns.py: business nouns in kernel
  [X] 51 business-noun hit(s) in kernel
```

**命中分布**：`kernel/algo/__init__.py` 1 处 + `kernel/algo/backtest.py` 50 处（`factor` 42 / `sharpe` 8 / `drawdown` 2 / `backtest` 4 / `calmar` 4 / `portfolio` 1）。命中词表见 `scripts/arch/check_kernel_nouns.py:75-97`（`factor` / `backtest` / `sharpe` / `calmar` / `drawdown` / `portfolio` 等）。

---

## §4 🔴 新发现：门禁红线的归属、三重防线缺失，以及一次「建议被实测推翻」的修正

> **本节是本次评估的唯一新发现，此前未见于任何文档。**
> §4.1–§4.2 讲红线归属与防线缺口；§4.3–§4.4 讲**一次自我修正**——初判「`kernel/algo` 是可删的死代码」，被实测推翻。

### 4.1 归属

| 项 | 值 |
|---|---|
| 引入 commit | `6f3e727c`（2026-11-14，`feat(kernel): prepare.py 评估算法提取为 kernel/algo/backtest（C 方案）`）|
| 引入文件 | `src/agentnodes/kernel/algo/{__init__.py, backtest.py}`（9 + 307 行）|
| 设计文档 | `docs/design/prepare-algo-kernel-extract-20261114.md` |
| **该文档 §6「验证表」只列了 5 条 pytest 命令** | **未列 `batch_gate`** —— 红线当时未被拦截 |
| 影响分支 | `ui-workbench` · `platform-kernel` · `u6-2-terminal-cleanup`（**`main` 不含** `kernel/algo`，干净）|

### 4.2 三重防线同时缺失

| 防线 | 状态 | 证据 |
|---|---|---|
| ① pytest 覆盖 | ❌ **无** | `tests/test_scripts_arch.py` 23 个用例覆盖 dangling / literals / layers / diagram，**零个 nouns 用例**（实测字符串 `nouns` 在该文件 0 命中）|
| ② CI 跑 batch_gate | ❌ **无** | `.github/workflows/*.yml` 引用 `ruff` / `pyright` / `pytest`（均 YES），引用 `batch_gate` / `check_kernel_nouns` / `check_dangling_imports`（均 NO）|
| ③ 手动 batch_gate | ⚠️ 有，但**未被执行** | `scripts/batch_gate.cmd:302-310` 有 `:step_kernel_nouns` 步骤；`check_kernel_nouns.py` 自带 `EXEMPT_FILES` / `EXEMPT_LINES` 豁免机制（**当前两者均为空**）|

> **判读**：这道门禁的唯一保护是「提交前手动跑一次 batch_gate」（R-007 §2.2）。`6f3e727c` 绕过了它，于是红线潜伏 2 天，直到本次全量核查才暴露。
>
> **门禁有效性反证（R-019，已实跑）**：门禁本身**是有效的**——本次用 gate 的真实 API 做了注入检验：
> | 检验项 | 结果 |
> |---|---|
> | gate 自带 `--selftest` | ✅ rc=0，5 条断言通过 |
> | 干净目录 → 注入一条 `backtest` 违规 | ✅ 命中数 0 → 1，报出文件与词 |
> | `EXEMPT_LINES` 逐条豁免 | ✅ 命中数 1 → 0（机制生效，非摆设）|
> | `EXEMPT_FILES` 整文件豁免 | ✅ 命中数 1 → 0 |
> | 目录名扫描（批次 8/1-F 扩展）| ✅ `kernel/backtest_utils` 目录名被命中 |
>
> ⇒ **结论**：门禁能力没问题，缺的是**执行纪律 + 自动化兜底**（两道防线都没接）。这决定了 §6.1 的动作是「接线」而不是「修门禁」。

### 4.3 附带发现：`kernel/algo` 是「零消费者」，但**不是可随手删的死代码**

| 项 | 实测 |
|---|---|
| 生产消费者（`src/` + `scripts/` + `webui/frontend/src/`）| **0** |
| 唯一消费者 | `tests/test_backtest_no_lookahead.py:20`（`from agentnodes.kernel.algo import evaluate`）|
| 原始来源 | `templates/prepare.py` —— tracked 版已随 W1 `9140f6c3` 移除；本地副本是**未跟踪**文件（设计文档 §0.2）|
| 设计文档自述 | 「prepare.py 运行时消费者：**无**」（§1 现状真值表）|

> **修正（重要）**：初次评估把此处判为「307 行死代码 + 门禁破，建议直接删」。**该结论已被实测推翻**——见 §4.4。

### 4.4 🔴 修正：`kernel/algo` 承载着**全仓唯一**的无前视修复，不可删

**背景**：`prepare.py` 的回测模拟存在「1 根 K 线前视偏差」——用 `close[i]` 选出的权重却赚取了 `close[i]` 当天的收益。2026-09-05 的 `d3ad9d23` 修过一次（引入 `pending_weights` 延迟生效），但 **W1 `9140f6c3` 删掉根 `templates/` 时把带修复的版本一起删了**；当前 tracked 的两处副本又都是**修复前的旧版**。

**本次实测**：把各候选版本喂同一组数据（常数因子 + 单日 +50% 跳价），直接测「调仓日 i=20 的跳价是否进 NAV」——这是 `tests/test_backtest_no_lookahead.py` 钉的那条性质：

| 版本 | 形态 | 实测结果 |
|---|---|---|
| `06bb37a3`（2026-08-28，修复前）| 先调仓后结算 | ❌ **LOOKAHEAD_BUG**（调仓日跳价被赚取）|
| `d3ad9d23`（2026-09-05，首次修复）| `pending_weights` 延迟生效 | ✅ NO_LOOKAHEAD_OK |
| `299be60e`（2026-09-06）| `pending_weights` | ✅ NO_LOOKAHEAD_OK |
| `9140f6c3^`（最后 tracked 祖先）| `pending_weights` | ✅ NO_LOOKAHEAD_OK |
| `testcase/golden/templates/prepare.py`（**当前 tracked**）| 先调仓后结算 | ❌ **LOOKAHEAD_BUG** |
| `testcase/samples/01_yaml_backtest/input/prepare.py`（**当前 tracked**）| 先调仓后结算 | ❌ **LOOKAHEAD_BUG** |
| **`kernel/algo/backtest.py`（当前 tracked）** | 先结算后调仓（重排式）| ✅ **NO_LOOKAHEAD_OK** |

**结论（三条，逐条可验证）**：

1. **修复可从 git 历史恢复** —— `git show 9140f6c3^:templates/prepare.py` 取出的版本**能通过**该性质检验（`pending_weights` 机制，与 kernel/algo 的「重排」机制不同但等价）。
   ⇒ 所以「删掉 kernel/algo 就永久丢失修复」**不成立**——但**需要先把它落到某个 tracked 位置**（见 §6.1 方案 b）。
2. **但当前两处 tracked 副本都是带 bug 的旧版** —— `testcase/golden/` 与 `testcase/samples/` 里的 `prepare.py` 实测均为 LOOKAHEAD_BUG。**仓库当前状态是「唯一正确的实现零生产消费者，两份被复制的代码带着 bug」**。
3. **修复的两种机制不可混用** —— kernel/algo 用「先结算收益再调仓」（重排），历史版用「pending_weights 延迟换手」（延迟生效）。二者等价但代码形态不同；直接「把 golden 换成历史版」需要确认 `pending_weights` 版在 `299be60e` 之后没有其他语义漂移。

> **对处置方案的影响**：§6.1 原推荐的「直接删」**作废**——它会连同唯一的正确实现与那 2 条钉住该性质的测试一起消失，而仓库里剩下的两份副本仍带 bug。正确处置见 §6.1 的方案 b。

---

## §5 遗留项清单（按影响排序）

| # | 项 | 影响 | 性质 | 证据 |
|---|---|---|---|---|
| 1 | `kernel/algo` 51 处 nouns 命中，batch_gate 红 | 🔴 违反 R-001/R-007；且**不能直接删**（见 §4.4，唯一正确实现）| **缺陷**（非设计）| §4.1–§4.4 |
| 2 | `u6-2-terminal-cleanup` 5 commit 未合并 | 🔴 双引擎路径并存；`loop.py` 仍 1,581 行 | 决策待定 | `git cherry` 全 `+`；`step_adapters.py` / `runtime/policy.py` / `test_engine_parity.py` / `test_legacy_engine_net.py` 均在 ui-workbench 上 |
| 3 | **两处 tracked `prepare.py` 副本实测仍带前视 bug** | 🔴 `testcase/golden/` 与 `testcase/samples/` 的副本均为修复前版本；仓库当前「唯一正确实现零消费者 + 两份复制品带 bug」 | **缺陷**（新发现）| §4.4 |
| 4 | phase1 D1 未达成（无 edit 工具）| 🔴 第一阶段「通用 agent 开箱可用」核心判定线未闭合 | 缺口 | `file_tools.py` |
| 5 | `build_autoresearch_workflow_config` 零调用 + 8 个悬空工具引用（被豁免遮住）| 🟠 门禁豁免下的病灶 | 缺口 | §2.4 |
| 6 | W5 假基因清理未做 | 🟠 RSI 开工的既定前置（引擎已收敛，前置条件已满足但没做）| 缺口 | `genome_version.py` |
| 7 | B1 验收门 5：契约零挂载 + `max_drawdown`/`max_dd` 三名并存 | 🟠 契约机制从未真正生效 | 缺口 | §2.3 |
| 8 | R3：RSI 四源同指 + `study.round_done` 死 kind | 🟠 RSI 证据源仍是单源，闭环缺口未补 | 缺口 | §2.4 |
| 9 | 批次 13 S3-b2 未完 + `loop.py` 目标 ≤650 未达 | 🟡 上帝对象仍在 | 缺口 | §2.2 |
| 10 | `.prompts` 17 + `.skills` 26 量化资产残留 | 🟡 已决议延期 | 延期 | §2.2 |
| 11 | CI 不跑 batch_gate / pytest 不覆盖 nouns | 🟡 **门禁体系自身的缺口** | 流程 | §4.2 |
| 12 | **`.rules/rules.md` §1.1 的层编号与门禁口径相反** | 🟡 读「L3→L2」会得到相反含义 | 文档 | §1.1 |
| 13 | `docs/INDEX.md` 的 design / archive 份数与实测不符 | 🟡 索引漂移 | 文档 | §1.4 |

---

## §6 建议的下一步（**待批准，本文件不实施**）

### 6.1 立即（半天级，低风险）

| # | 动作 | 说明 | 纪律 |
|---|---|---|---|
| **1** | **处置 `kernel/algo`（三选一，推荐 b）** | **a. 加 `EXEMPT_FILES` 豁免** —— 最小改动，但等于承认「kernel 可以有量化算法」，与 R-001 的方向冲突，且**不修复**「两份副本带 bug」这个更实的问题。<br>**b. 迁到 `apps/algo/`（推荐）** —— 业务域算法本就属业务层；`apps/` 已有 14 个文件带同族业务名词且**合法**（门禁只扫 `kernel/`，实测 `KERNEL = ROOT/"src"/"agentnodes"/"kernel"`）。改动最小集：`git mv` + 测试 import 1 行 + `kernel/__init__.py:7` 删 1 行 + 架构图回写；`layer_matrix` **无需登记**（`agentnodes.apps` 已登记为 L2，最长前缀兜底）。<br>**c. 删掉** —— ❌ **作废**：会连同唯一的正确实现与钉住它的 2 条测试一起消失，而仓库里两份 tracked 副本仍带 bug（§4.4）。 | 若选 b，需**同时**处理 §4.4 的副本问题（两份带 bug 的 `prepare.py`）——否则只是把正确实现搬了个地方，bug 副本照旧在仓库里 |
| **2** | **修复两份 tracked `prepare.py` 副本的前视 bug** | `testcase/golden/templates/prepare.py`（480 行）与 `testcase/samples/01_yaml_backtest/input/prepare.py`（473 行）实测均为 LOOKAHEAD_BUG。修法：从 `git show 9140f6c3^:templates/prepare.py` 取回 `pending_weights` 机制（实测能通过同一性质检验），或直接改用 kernel/algo 的重排机制 | 二者都在 `testcase/`（`pyproject.toml` 的 `testpaths=["tests"]` 天然排除收集），**不受 pytest 保护** → 改完需手工验证，且**不能只靠 CI** |
| **3** | **把 nouns 门禁接进 pytest** | 在 `tests/test_scripts_arch.py` 加 `test_kernel_nouns_real_repo_clean`，仿照既有的 `test_dangling_real_repo_clean` / `test_literals_real_repo_clean` | 这是本次事故暴露的真缺口：门禁只靠手动跑等于没有 |
| **4** | **CI 增一条 batch_gate job** | 至少跑 8 步结构类 | 与 §4.2 的三重防线对应 |
| **5** | 修 `rules.md` §1.1 的层编号 | 与门禁口径对齐（foundation=L4 / kernel=L3 / apps=L2 / interfaces=L1） | 顺手；口径歧义会让「L3→L2」被读反（§1.1）|
| **6** | 修 `docs/INDEX.md` 的 design / archive 份数 | 50→104、264→266 | 顺手，不单独占 commit |

### 6.2 需拍板

| # | 决策项 | 说明 |
|---|---|---|
| 5 | **`u6-2-terminal-cleanup` 是否合并** | 唯一能同时解掉「双引擎并存」与「loop.py 1,581 行」的分支；与 ui-workbench 共享祖先，`git cherry` 全 `+` 无等价 patch |
| 6 | **§6 设计决策（10 条）** | `ui-workbench-design-20261115.md` §6：**6.1 转向确认**是 U-A 总前置；**6.9（灰态 vs 等后端）**卡 U-B 排期；**6.10（玻璃去留）**为 §11 新增 |
| 7 | **B1 验收门 5** | 给 `risk_controller` 挂第一个真契约 + 收敛 `max_drawdown`/`max_dd` 命名 |
| 8 | **phase1 D1 是否补齐** | 补 `edit` 工具（C6/C7），还是修订 phase1 的 DoD 定义 |
| 9 | **批次 5（W5 + R3）优先级** | RSI 开工的前置；是否排在 UI 轨道之前 |
| 10 | **`kernel/algo` 迁 `apps/` 后，谁来消费它** | 迁走只是解决门禁与层级，不解决「零消费者」。若无人接，则本批应直接并入 §6.1-2 的副本修复（保留一份正确实现 + 2 条测试），而非长期悬空 |

### 6.3 暂不动

- templates 去研究化（旧批次 7）——已决议延期，需确认是否仍在优先级内。
- `apps/goal/workflow_config.py` 的 9-agent 拓扑 —— 与 §6.2-9 绑定，R3 决策时一并处理。
- Trace / RSI 视图实施 —— 按 §6.7 明确排除出本轮。

---

## §7 本次不做的事（明确记录）

1. **不改任何生产代码** —— 本文件只评估；§6.1 的四项动作均待批准。
2. **不合并 `u6-2-terminal-cleanup`** —— 按先前用户指令「我还不想合并，想保留着」。
3. **不动 `kernel/algo` 的代码** —— 处置方案（豁免 / 迁 apps/ / 删）待批准；§4.4 已排除「直接删」。
4. **不改两份 tracked `prepare.py` 副本** —— 它们带前视 bug 是**实测事实**，但修法涉及「取回历史版本 vs 改用重排机制」的选择，待批准。
5. **不重录前端视觉基线** —— 47 张 PNG 全为 dark（light 主题零基线），但 A-0 批属 U-A，受 §6.2-6 的 6.1 总前置阻塞。
6. **不删 `templates/.prompts` / `.skills`** —— 已决议延期至第三步。

---

## 附录 A：本次实跑的验证命令

```bat
:: 后端全量
python -m pytest tests -q -p no:hypothesispytest -p no:hypothesis -n 8 --dist loadgroup --timeout=600
::   → 7302 passed / 115 skipped / 4 xfailed / 0 failed（125.59s）

:: 层级门禁
python scripts\arch\check_dangling_imports.py --layers
::   → 上行违规 0 · L3→L2 = 0 · 悬空 0

:: 全量门禁
scripts\batch_gate.cmd
::   → FAIL（仅第 7 步 kernel nouns 红；其余 7 步 OK，收集 7421）

:: 前端
cd webui\frontend && npm run test:coverage
::   → 122 文件 / 1323 用例全绿；All files 89.60 / 81.85 / 79.12 / 89.60

:: 自建 probe
python tmp\probe\doc_verify.py
::   → 45 条事实断言通过 / 1 条 "BAD"（该条即 §4.2 断言的缺口本身）
python tmp\probe\doc_paths2.py
::   → 64 条路径/行号引用全部可解析
python tmp\probe\semantic_test3.py
::   → 7 个候选 prepare.py 版本的前视性质实测（§4.4 的决定性实验）
python tmp\probe\layer_numbering.py
::   → 层编号两套口径的判定（§1.1）
python tmp\probe\gate_injection2.py
::   → R-019 注入检验：门禁有效性 / 豁免机制 / 目录名扫描（5 项全绿，§4.2）

:: 规模统计
python tmp\probe\progress_stats.py
python tmp\probe\final_check2.py
```

## 附录 B：本次新增的 probe 脚本

| 脚本 | 用途 |
|---|---|
| `tmp/probe/progress_stats.py` | 分层 / 子系统 / 文档 / git 规模统计 |
| `tmp/probe/doc_verify.py` | **本报告每条断言的复核网**（45 条）|
| `tmp/probe/doc_paths2.py` | 文档内文件路径 / 行号引用解析核对（64 条通过）|
| `tmp/probe/final_check2.py` | 模板残留 / 工作区状态 / CI 覆盖 / 契约挂载数 |
| `tmp/probe/algo_consumers.py` | `kernel/algo` 消费者普查 + nouns 门禁历史判定 |
| `tmp/probe/prepare_drift.py` | 三处 `prepare.py` 副本 vs kernel/algo 的修复特征比对 |
| `tmp/probe/lookahead_diff.py` | `_simulate_backtest` 逐行 diff（golden vs kernel/algo）|
| `tmp/probe/fix_recoverable.py` | `templates/prepare.py` 全部 6 个历史版本的无前视语义判定 |
| `tmp/probe/semantic_test3.py` | **决定性实验**：对 7 个候选版本实测「调仓日跳价是否被赚取」|
| `tmp/probe/layer_numbering.py` | 两套层编号口径的判定（以门禁矩阵反推）|
| `tmp/probe/gate_injection2.py` | **R-019 注入检验**：nouns 门禁的有效性 / 豁免机制 / 目录名扫描（5 项全绿）|
| `tmp/probe/logsum.py` | pytest 日志摘要读取 |

> `tmp/` 已被 gitignore，probe 脚本随用随弃（与 `ui-workbench-design-20261115.md` §7 约定一致）。

## 附录 C：本次评估的方法论说明

**为什么每条结论都要跑一遍而不是读文档**：本次评估推翻了两条**基于文档与初步 grep 的判断**——

1. 「`kernel/algo` 是可删的死代码」→ 实测证明它是全仓唯一的无前视修复载体（§4.4）；
2. 「层编号按 rules.md §1.1」→ 实测证明门禁用的是相反口径（§1.1）。

两条的共同教训：**文档/提交信息里的断言必须用可执行判据复核**。这正是 R-012「现状实测（不是印象）」与 R-019「门禁的价值 = 注入违规后会不会红」的同一精神——把「我以为」换成「我测过」，并且**在报告里显式标注哪条结论被推翻了**，而不是悄悄改口。

**复核网的两层结构**：
- `doc_verify.py`（45 条）—— 核对报告里每个事实断言（数字 / 路径 / 缺失 / 门禁状态）；
- `doc_paths2.py`（64 条）—— 核对报告里每个**文件路径与行号引用**可解析、行号在文件范围内。

> 两条脚本合计 **109 条断言**，其中唯一的 "BAD" 是 `test_scripts_arch.py` **没有** nouns 用例 —— 这正是 §4.2 要报告的缺口本身（断言写成「有」是刻意的反向检验）。
