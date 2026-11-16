# 计划：ui-workbench 合并 main + 框架与理念 v1.2（R-014 方案先行）

> **性质**：实施设计 / 合并方案 · **日期**：2026-11-16 · **基线**：`ui-workbench` @ `601009a2`
> **前置**：已提交进度评估 `docs/design/project-progress-assessment-20261116.md`（commit `601009a2`）
> **本文档只出方案，不执行任何步骤。** 每一步的「验收判据」都写明了「哪个数字/断言会变」，等确认后再动手。
> **口径**：本文件所有数字均为本次实跑所得（git 溯源 / AST 扫描 / 门禁实跑），非文档转述。

---

## §0 结论摘要

三件事，**顺序不能换**：

| # | 事 | 结论 | 风险 |
|---|---|---|---|
| **1** | `ui-workbench` → `main` | ✅ **可 fast-forward**（main 是 ui-workbench 的祖先，534 commit 无分叉） | 低 —— 但 main 落后 534 个 commit、含 67,918 行 `src/strategy_research` 删除，**合并本身不改代码**，风险全在验证 |
| **2** | 架构变化梳理 | ✅ **已完成盘点**（§2），可直接喂给 v1.2 | 无（只读） |
| **3** | `框架与理念 · v1.2.html` | ⚠️ **不是「改 v1.1」而是「重做内容 + 复用样式」** | 中 —— v1.1 的 body 里全是 2026-11-05 的快照数字，已被 2026-11-16 的代码推翻 |

**为什么 3 必须排在 1 之后**：v1.2 要展示的是「合并后的项目长什么样」，而合并会改变 `main` 的基线指针。先合再看，才能保证 v1.2 里引用的每个数字都对应合并后的真实状态。

**为什么 3 不能只做「更新数字」**：v1.1 的 §02 七条理念里，**每一条的「检查」栏都已过时**（实测见 §2.3）；§03 三条回路的存活状态全变了；§04 构件落位表有 3 行状态反转；§06 九项工作 4 项完成、1 项部分、1 项仍未动；§08 执行序的批次已全部跑完或明确延期。**只换数字不改叙述 = 一份自相矛盾的新文档。**

---

## §1 合并方案

### 1.1 拓扑事实（实测）

| 项 | 值 | 证据 |
|---|---|---|
| `merge-base(main, ui-workbench)` | `39fcb520` | `git merge-base` |
| main 是否 ui-workbench 的祖先 | **是** | `git merge-base --is-ancestor main ui-workbench` → rc=0 |
| commit 数差 | main 独有 **0** / ui-workbench 独有 **534** | `git rev-list --left-right --count` |
| `origin/main` | `39fcb520`（= main，本地与远端同步）| `git rev-parse` |
| `origin/ui-workbench` | `a15280b0`（落后本地 1 个 commit：本次的 `601009a2`）| — |

⇒ **纯 fast-forward，无冲突可能**（main 无独有 commit，不存在三方分叉）。`git merge --ff-only ui-workbench` 是安全形态。

### 1.2 变更规模（`main..ui-workbench`）

**总账**：3058 文件 / **+195,428 行 / −37 行**。

> 「−37 行」看着很小，但那是**净差**。实际有 **646 个文件被删除**（`src/` 占 626），同时 **688 个新增**。净差小是因为**删除的是 67,918 行旧包、新增的是 36,274 行新包** —— 一次**整体换包**（`src/strategy_research` → `src/agentnodes`）。

**按目录**：

| 路径 | 文件 | +行 | −行 | 性质 |
|---|---:|---:|---:|---|
| `src/strategy_research/` → `src/agentnodes/` | 633 → 164 | 36,274 | 67,918 | **包名替换 + 四层重组** |
| `testcase/tests/` | 94 | 22,679 | 0 | 归档区（golden/samples，不被 pytest 收集）|
| `.zcode-research/sm/` | 14 | 20,904 | 0 | 研究中间产物 |
| `docs/design/` | 75 | 19,415 | 0 | 设计文档 |
| `tests/baselines/` | 44 | 13,672 | 0 | 基线数据 |
| `docs/archive/` | 82 | 12,970 | 0 | 归档文档 |
| `scripts/arch/` | 14 | 4,964 | 0 | **架构门禁（新增整套）** |
| `webui/frontend/` | 30 | 4,129 | 32 | 前端 |
| `README.md` | 1 | 118 | 747 | 说明文档重写 |

**关键路径存活**（`git ls-tree`）：

| 路径 | main | ui-workbench |
|---|---:|---:|
| `src/agentnodes/` | **0** | 525 |
| `docs/design/` | **0** | 104 |
| `scripts/arch/` | **0** | 14 |
| `webui/frontend/src/` | 282 | 298 |
| `tests/` | 639 | 580 |

⇒ main 上**根本没有** `src/agentnodes`、`docs/design`、`scripts/arch` —— 这些全是这 534 个 commit 引入的。合并 = 把整个新骨架搬进 main。

### 1.3 合并后必须重跑的五道验证（R-007 扩展）

| # | 命令 | 判据 | 当前基线（ui-workbench 实测）|
|---|---|---|---|
| 1 | `python -m pytest tests -q -p no:hypothesispytest -p no:hypothesis -n 8 --dist loadgroup --timeout=600` | 0 failed | **7302 passed / 115 skipped / 4 xfailed / 0 failed**（125.6s）|
| 2 | `scripts\batch_gate.cmd` | ALL GREEN | ⚠️ **当前 FAIL**（仅第 7 步 kernel nouns 红，51 处）|
| 3 | `python scripts\arch\sync_architecture_diagram.py` | 无漂移 | 当前一致（`test_architecture_diagram_matches_code` 在全量里绿）|
| 4 | `cd webui\frontend && npm run test:coverage` | 全绿 + 覆盖不回退 | **122 文件 / 1323 用例全绿，89.60%** |
| 5 | `python scripts\arch\check_dangling_imports.py --layers` | 上行违规 0 | **0**（L3→L2 = 0，悬空 0）|

> **第 2 项现在就是红的** —— 这不是合并引入的，是 `6f3e727c`（2026-11-14）引入 `kernel/algo` 时带进来的，已潜伏 2 天。详见进度评估 §4。
> **决策点**：合并前先修这处红线，还是合并后修？**我的建议：合并后修**（红线与合并无关，合并会引入 534 个 commit 的噪音，让「是合并造成的还是本来就有的」变得难分辨；但**必须在合并后立即修**，不能让它带着红线进 main）。

### 1.4 合并操作序列（含安全措施）

| 步 | 动作 | 说明 |
|---|---|---|
| 1 | `git checkout main` | 工作区当前干净（`601009a2` 已提交）|
| 2 | `git merge --ff-only ui-workbench` | **必须用 `--ff-only`** —— 若因某种原因出现分叉，命令会直接失败而不是自动造 merge commit（更安全）|
| 3 | 跑 §1.3 的五道验证 | 全绿才继续 |
| 4 | `git push origin main` | ⚠️ **推 main 是外向操作** —— 需你明确授权（`git push --force` 是红线，普通 push 也需确认）|
| 5 | `git checkout ui-workbench` + 保持它作为工作分支 | 分支不删，保留 534 个 commit 的独立轨迹 |

> **回滚方式**：FF 合并后 `git reset --hard 39fcb520` 即可回到合并前 —— 但这会丢失合并后的工作区内容，**故回滚只在「合并后未 push」时可用**。push 之后只能靠 `git revert` 或从 `origin/ui-workbench` 恢复。**建议：push 前先在本地跑完五道验证。**

### 1.5 合并前需要你确认的三件事

1. **是否授权 `git push origin main`**？（本地合并可先做，push 等你点头）
2. **`u6-2-terminal-cleanup`（5 commit）是否一并合并？** —— 它基于 `platform-kernel` 而非 ui-workbench，合并会产生真正的 merge commit（不是 FF），且引入冲突面。建议**不**现在合：进度评估已把它列为遗留项 #2。
3. **`batch_gate` 的红线**：合并后立即修（§6.1-1/2/3），还是接受 main 短暂带红？

---

## §2 架构变化盘点（v1.1 → 现状）

> 全部实测。v1.1 的基线 commit = `e44c6869`（2026-11-06，`框架与理念 · v1.1.html` 最后修改）。

### 2.1 层归属与体量

| 层（门禁口径）| 目录 | v1.1 文件 | 现在 | Δ |
|---|---|---:|---:|---:|
| L1 | `interfaces/` | 109 | 119 | +10 |
| L2 | `apps/` | 15 | **79** | **+64** |
| L3 | `kernel/` | 221 | **191** | **−30** |
| L4 | `foundation/` | 66 | 73 | +7 |
| — | `evals/` | 7 | 10 | +3 |
| — | `templates/` | 53 | 51 | −2 |
| — | **合计** | **473** | **525** | **+52** |

**一句话读法**：**L2 涨 +64、L3 降 −30** —— 业务内容从 kernel 迁往 apps（这正是 R-001「kernel 零业务名词」的方向），但 apps 同时新增了 `goal`（R1 恢复，18 文件）+ `agent_plugins` + `scheduled_research` + `swarm_presets`。

**kernel/ 子包增删**：

| 变化 | 子包 | 依据 |
|---|---|---|
| ➕ 新增 | `algo/` | `6f3e727c`（2026-11-14，C 方案）—— 量化评估算法，**但它正是门禁红线的来源** |
| ➖ 迁出 | `scheduled_research/` | 批次 8（`93fc98e4`）—— 迁到 `apps/` |

**apps/ 子包新增**：`agent_plugins` / `goal` / `scheduled_research` / `swarm_presets` —— **全部是 v1.1 时代还不存在的**（v1.1 的 §05 目录树里 `apps/` 只有 `goal/`（待恢复）+ `chat/` + `builtin_tools/`）。

### 2.2 v1.1 时代不存在的架构级构件（现在全部存在）

| 构件 | 路径 | 行数 | 引入 |
|---|---|---:|---|
| B1 节点产物契约 | `kernel/workflow/contract.py` | 317 | `a15280b0`（2026-11-16）|
| LoopEngine 统一引擎 | `kernel/agent/loop_engine.py` | 562 | 批次 11/12 |
| LoopSpec 聚合 | `kernel/agent/loop_spec.py` | 284 | 批次 12（S2）|
| loop_assembly 双工厂收口 | `kernel/agent/loop_assembly.py` | 89 | phase3 |
| guardrails 护栏 | `kernel/agent/guardrails.py` | 114 | phase5 |
| StepHandler 协议 | `kernel/agent/_core_types.py` | — | 批次 9（B1 协议统一）|
| ctx_hash_id 上下文指纹 | `kernel/agent/agent_telemetry.py::compose_ctx_hash_id` | — | `7e68ac77`（2026-11-12）|
| 架构门禁全套 | `scripts/arch/`（14 文件）| — | 批次 4d / 8 |
| 层级矩阵唯一事实源 | `scripts/arch/layer_matrix.py` | 207 | `layered-skeleton-design-20261103` |
| 架构图同步器 | `scripts/arch/sync_architecture_diagram.py` | 390 | 批次 4d |

### 2.3 v1.1 §06 九项工作项的当前真实状态（实测）

| 项 | v1.1 时的状态 | **现在** | 实测判据 |
|---|---|---|---|
| **W1** 装配期能力自检 | 待开工 | ✅ **完成** | `requires` 声明 95 处；`apps/capabilities.py` 注册带 requires |
| **W2** 成本/延迟并列维度+双闸 | 待开工 | ✅ **完成** | cost 双闸 43 处；latency 采集 5 处 |
| **W3** held-in/out 双集双门 | 待开工 | ✅ **完成** | `kernel/evolution/holdout.py` 存在；holdout 相关 313 处 |
| **W4** 完整性指纹 + 循环驱动 | 待开工 | ⚠️ **部分** | 指纹 114 处；`build_rsi_loop` 12 处；`cycle_started` 12 处；**cron 兜底 0 处** |
| **W5** 假基因清理 | 待开工 | ❌ **未做** | `deactivate(` 生产调用 **0 处** |
| **W6** 事件 hash chain | P2（可选）| ❌ **未做**（按 D5 拍板延到 P2）| hash chain 相关 **0 处** |
| **R1** goal 恢复 | 待恢复 | ✅ **完成** | `apps/goal` 18 文件；goal.* kind 67 处；`goal.settled` 14 处 |
| **R2** chat 接线 | 待接线 | ✅ **完成** | slash_goal 32 处；GoalPanel 74 处；goal SSE 48 处；goal API 路由 2 处 |
| **R3** journal/scoreboard 端口 + preset 化 | 待接线 | ❌ **未做** | `journal_source`/`scoreboard_source` 模块 **0 处**；**`rsi_wiring.py:219` 四源仍同指** `EventStoreReportSource` |

> **v1.1 §04 构件落位表的 3 处状态已反转**（对照原表）：
> | 构件 | v1.1 写 | 现在 |
> |---|---|---|
> | 循环驱动（第 0 步）| ❌ W4 | ⚠️ 部分（CLI + 事件有，cron 兜底无）|
> | 任务级判据证据 | ⚠ 待恢复（R1），端口接入（R3）| ✅ R1 已恢复 / ❌ R3 端口未接 |
> | 物理门禁 + 评估指纹 | ✅ 门禁已有 · ❌ 指纹 W4 | ✅ 门禁 8 步齐备（**其中 1 步当前红**）· 指纹已有 |

### 2.4 v1.1 之后 md 底稿新增、HTML 尚未收录的三块内容

`docs/design/framework-and-principles-20261105.md`（HTML 的上位源）在 `e44c6869` 之后又更新了 3 个 commit，**HTML 里 0 命中**：

| commit | 新增内容 | md 位置 | HTML 命中 |
|---|---|---|---|
| `442b6ab5` | §0.5 概念对照（Loop Engineering × Graph Engineering）+ 病理↔闸门对照表 | `:25` / `:80` | 「Loop Engineering」0 命中 |
| `1a9050f2` | §0.5 收束 —— 三条合法信道定理（Loop↔Graph 接口最小化）| `:58` | 0 命中 |
| `6239bb27` | §3.5 执行面与理念元规则裁决落档（B1/B2/B3/C 可调参数/D 语义门禁）| `:566` | 0 命中 |

⇒ **这是 v1.2 存在的最直接理由**：v1.1 的 HTML 已经落后它的 md 底稿 3 个 commit。

### 2.5 v1.1 HTML 自身的两处缺陷（v1.2 顺手修）

| # | 缺陷 | 位置 | 修法 |
|---|---|---|---|
| 1 | **3 条关联文档链接失效**（都少 `design/` 前缀）| `:719` / `:720` / `:721` | 改为 `design/framework-and-principles-20261105.md` 等 |
| 2 | **全篇 0 个 CSS 变量**，30 个 hex 硬编码 | `<style>` L7–311 | 抽成 `:root{ --layer-g; --layer-e; --layer-p; --barrier; --bridge; --phys-l1..l4; --ok; --warn; --bad; }`（参照 `architecture-v2-summary.html:8–14` 的写法）|

> 缺陷 2 对「v1.1 / v1.2 对照」是**刚需**：不变量化的两版无法做颜色对照或主题切换。

### 2.6 v1.1 HTML 里已被推翻的硬编码快照数字

| 位置 | v1.1 写的 | 现在实测 |
|---|---|---|
| `:426` | L1 `108 文件 · 23.0k 行` | **119 文件** |
| `:435` | L2 `15 文件 · 4.5k 行` | **79 文件** |
| `:444` | L3 `189 文件 · 36.4k 行` | **191 文件** |
| `:453` | L4 `59 文件 · 10.0k 行` | **73 文件** |
| `:593` | `goal/` 标注「R1 恢复（19 文件）」 | **18 文件**（且已恢复，非「待恢复」）|
| `:594` | `chat/` 标注「产品策略（222 行，维持）」 | 需复核 |

---

## §3 v1.2 实施方案

### 3.1 产出物与位置

| 项 | 值 |
|---|---|
| 新文件 | `docs/框架与理念 · v1.2.html` |
| v1.1 原件 | **保留不动**（沿用仓库既有惯例：`git log --follow` 显示 v0.1→v1.0→v1.1 全是**改名快照**推进，从不原地改）|
| md 底稿 | `docs/design/framework-and-principles-20261105.md` → 更新到 v1.2（**它是上位源**）|
| 门禁 | ❌ **无脚本生成 / 无 CI 校验** —— `sync_architecture_diagram.py:36` 只管 `docs/architecture-diagram.html`。v1.2 需**人工校对数字** |

### 3.2 结构设计：v1.1 的 9 节 → v1.2 的 11 节

| v1.2 节 | 标题 | 来源 | 性质 |
|---|---|---|---|
| 00 | 一句话 | v1.1 §00 微调（加「统一引擎单轨」「门禁 8 步」）| 改写 |
| **01** | **概念对照 · Loop × Graph**（新增）| md §0.5（`442b6ab5` / `1a9050f2`）| **新增** |
| **02** | **双轴正交总图 · v1.1/v1.2 并排对照** | v1.1 §01 骨架 + 现状数字 | **改写**（用户核心诉求）|
| 03 | 七条理念 · 每条 = 张力 + 规则 + **检查栏已更新** | v1.1 §02 + §2.3 实测 | **改写** |
| 04 | 三条反馈回路 · 时标分层 | v1.1 §03（存活状态全变）| **改写** |
| 05 | 构件落位 · 语义轴 × 物理轴 | v1.1 §04（3 行状态反转）| **改写** |
| 06 | 落地后的目录形态 | v1.1 §05（`apps/` 4 个新子包 + `kernel/algo`）| **改写** |
| 07 | 工作项总表 · **完成度回填** | v1.1 §06（9 项实测状态）| **改写** |
| 08 | 决策点 D1–D6 · 全部已拍板 | v1.1 §07（保留 + 补「后续落点」行）| 保留 + 增补 |
| 09 | v1.0 执行序 · **实际执行结果** | v1.1 §08（批次已全部跑完/延期）| **改写** |
| **10** | **架构变化总账 · v1.1 → v1.2**（新增）| §2 全部实测 | **新增**（对照核心）|
| — | 图例 + 关联文档 | v1.1 末尾（修 3 条链接）| 修正 |

### 3.3 技术规格

| 项 | 规格 | 理由 |
|---|---|---|
| **零依赖** | 保持：无 `<script>` / `<svg>` / `<canvas>` / 外链 / CDN | v1.1 已达成；GEP-RSI v1.1 同一套语汇 |
| **绘图手法** | 保持「手绘 div」：`.sep-box` + `.harrow`（`::after` 三角）+ `.feedback-bar`（虚线）+ `.bridge`（chip 条）+ `.phy-stack`（`▼` 分层）+ `.pill` / `.table-wrap` / `.legend` | 本仓自创的可视化语汇，GEP-RSI v1.1 复用同一套 |
| **CSS 变量化** | 30 个 hex → `:root` 变量（§2.5 缺陷 2）| v1.1/v1.2 对照刚需 |
| **真 heading** | 分节从 `div.section-label` 改为 `<h2>` + 保留 `.section-label` 视觉 | v1.1 全篇只有 1 个 `<h1>`、0 个 `<h2>`（实测）→ 浏览器大纲 / 锚点跳转 / 无障碍 |
| **v1.1/v1.2 并排** | 新增 `.cmp-grid`（两列）：左 v1.1 灰显、右 v1.2 现状 | 用户诉求「对照一下」 |
| **数字来源** | 每个体量数字标注来源（实测 probe 脚本 / `git ls-tree`）| 避免再出现 v1.1 那种「快照数字过时无人发现」|

### 3.4 工序（R-020：删除类动作排最后；本批无删除）

| 工序 | 动作 | 验收判据 |
|---|---|---|
| **A** | 更新 md 底稿到 v1.2 | §2.4 的三块内容全部进 md；W/R 状态表回填实测值 |
| **B** | 建 `框架与理念 · v1.2.html`：复制 v1.1 的 `<style>` + 骨架 → 变量化 → 改 `<title>`/`<h1>`/版本徽标 | 文件存在；`var(--` ≥ 20 处；`<h2>` ≥ 10 个 |
| **C** | 重写 body（11 节）| 11 节齐全；每节有 v1.1/v1.2 对照 |
| **D** | 数字回填与核对 | 建一个 probe 脚本，**从代码现算**每个数字，逐个断言 HTML 里写的就是实测值（0 偏差）|
| **E** | 修 3 条关联链接 + 补 GEP-RSI 的 `.related` 块 | 链接解析脚本 0 悬空 |
| **F** | 收录进 `docs/INDEX.md`（新建 §2.7 已存在 → 并入）+ 提 commit | INDEX 链接 0 悬空 |

> **工序 D 是重点**：v1.1 的教训就是「快照数字硬编码在 HTML 里，无脚本校验，3 周内被现实推翻而无人发现」。**v1.2 必须配一个数字核对脚本**（哪怕只做一次性核对），否则 v1.3 会重蹈覆辙。

### 3.5 验收判据（可执行）

| # | 判据 | 命令/方法 |
|---|---|---|
| 1 | v1.2 文件存在，零依赖 | 断言 `<script`/`<svg`/`<canvas>`/`https?://` 全 0 |
| 2 | CSS 变量化 | `var(--` ≥ 20；硬编码 hex 从 30 降到 ≤ 12 |
| 3 | 真 heading | `<h2>` ≥ 10 |
| 4 | 11 节齐全 | 编号 00–10 连续无缺 |
| 5 | **数字 0 偏差** | 自建 probe：从代码现算 → 断言 HTML 里的每个体量数字与之相等 |
| 6 | 链接 0 悬空 | 解析 HTML 内所有 href/md 链接 |
| 7 | v1.1 原件未动 | `git diff --quiet HEAD -- "docs/框架与理念 · v1.1.html"` |
| 8 | md 底稿已同步 | md 标题版本 = v1.2；§0.5 / §3.5 在位 |
| 9 | INDEX 已收录 | INDEX 内该链接可解析 |
| 10 | **不影响门禁** | `scripts\batch_gate.cmd` 第 1 步 collect 锚点不变（docs 不影响）|

---

## §4 执行顺序与批次

| 批次 | 内容 | 依赖 | 产出 commit |
|---|---|---|---|
| **M1** | 合并 `ui-workbench` → `main`（FF）+ 五道验证 | §1.3 | `merge(main): ui-workbench 534 commit 快进合并` |
| **M2** | `u6-2-terminal-cleanup` 合并？ | **待你决定**（§1.5-2）| — |
| **V1** | md 底稿更新到 v1.2 | §2 全部实测 | `docs(design): 框架与理念 v1.2 底稿` |
| **V2** | v1.2 HTML：样式变量化 + 骨架 | V1 | `docs(ui): 框架与理念 v1.2 HTML（变量化 + 11 节骨架）` |
| **V3** | v1.2 HTML：body 11 节内容 + 数字回填 | V2 | `docs(ui): 框架与理念 v1.2 内容与对照` |
| **V4** | 数字核对脚本 + INDEX 收录 | V3 | `docs(index): 收录框架与理念 v1.2` |

> **M1 与 V1–V4 无依赖**（除 V3 需要 M1 后的真实状态）。若你想先看文档，M1 可延后到 V3 之前。

---

## §5 本批不做的事

1. **不改 v1.1 原件** —— 只新建 v1.2（沿用仓库改名快照惯例）。
2. **不 push `origin/main`** —— 除非你明确授权（§1.5-1）。
3. **不动 `u6-2-terminal-cleanup`** —— 待你决定（§1.5-2）。
4. **不修 `kernel/algo` 门禁红线** —— 那是进度评估 §6.1 的独立批次；本批只把它**记进 v1.2 的架构变化总账**（如实标注「门禁 8 步中 1 步当前红」）。
5. **不动任何生产代码** —— 本批只动 `docs/`。
6. **不改 `.rules/rules.md` 的层编号** —— 那是进度评估 §6.1-5 的独立动作；但 v1.2 **统一采用门禁口径**并在文内声明。

---

## §6 待你拍板

| # | 决策 | 影响 |
|---|---|---|
| 1 | **授权 `git push origin main`？** | M1 的最后一步；不授权则只做本地合并 |
| 2 | **M1 之前还是之后修 `kernel/algo` 红线？** | 我建议 **之后**（红线与合并无关，先合可让「谁造成的」更清晰）|
| 3 | **`u6-2-terminal-cleanup` 现在合还是继续留着？** | 你此前说「不想合并，想保留着」；若维持，本批只在 v1.2 标注其未合并状态 |
| 4 | **v1.2 的对照形态**：并排双栏（v1.1 左 / v1.2 右）vs 只做「变化总账」一节？ | 我建议**两者都要**：双栏放在 §02 总图，总账放 §10 |
| 5 | **v1.2 是否也加「数字自动核对脚本」（长期维护）？** | 我建议**是** —— 否则 v1.3 会重蹈「快照数字过时无人发现」 |

---

## 附录：本次实跑的验证命令

```bat
:: 合并可行性
git merge-base main ui-workbench
git merge-base --is-ancestor main ui-workbench
git rev-list --left-right --count main...ui-workbench
git diff --stat main..ui-workbench

:: 架构变化（§2）
python tmp\probe\merge_feasibility.py     :: 合并规模 / 目录分布 / 增删分类
python tmp\probe\arch_delta.py            :: 层归属 / 子包增删 / 体量 / 构件存在性
python tmp\probe\wr_status.py             :: W1–W6 / R1–R3 逐项实测

;; v1.1 HTML 结构（§3）
git log --follow --name-status -- "docs/框架与理念 · v1.1.html"
git grep -n -E "<h[1-6]|<script|<svg|<canvas|https?://" -- "docs/框架与理念 · v1.1.html"
```

> probe 脚本在 `tmp/`（gitignore，随用随弃）。
