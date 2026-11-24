# 输入框工具行 P0→P1 修复方案（R-014 · 待批准 · 零代码）

> 日期：2026-11-24
> 状态：方案。批准后才动代码；本文不改任何实现。
> 触发：用户「先修 1+2；8 个格式化图标这个功能直接去除不需要了，或者按照你说的改成『选中时浮出』或折叠；按 P0 → P1 出 R-014 实施方案」。
> 依据：`composer-toolbar-evaluation-20261124.md`（评估记录，4 个实证问题）。

## 0. 用户的三个决定（已锁定）

1. **先修问题 1（Plan/Build 持久化失效）+ 问题 2（角色下拉污染）** —— P0。
2. **8 个 Markdown 格式化图标直接去除**（不走「选中时浮出」/「折叠」备选）。
3. **按 P0 → P1 出方案** —— P0 修完后再做 P1。

### 去除格式化图标的能力取舍（需知情）

评估已核实：`slashCommands.tsx` 的 6 条 slash 命令**不覆盖** Markdown 格式化（`/goal` `/study` `/compact` `/agent` `/clear` `/help`），因此直接去掉 8 个图标会**完全丢失**加粗/列表/引用等插入能力，且当前**无快捷键、无命令面板**可替代。

**本方案的处理**：去图标，但**保留 `applyMarkdown` 能力并补标准快捷键**作为唯一入口——
- 保留 `Composer.tsx` 的 `applyMarkdown` 实现（并按 P1-3 修行首/多行问题）
- 新增 `Ctrl/Cmd+B / I / E`（粗体/斜体/行内代码）、`Shift+Ctrl/Cmd+7/8`（有序/无序列表）等标准编辑器快捷键
- 快捷键**仅在输入框聚焦且有选区时**生效，无选区时不拦截（避免误触）
- 与全局快捷键冲突检查：`useKeyboardShortcuts.ts` 现占 `Cmd+B`（切换右栏）——**必须在输入框内让位**（编辑器惯例），非输入区保持切换右栏

这样「去图标」达成（输入框上方不再有 8 个常驻小图标、375 宽下不再占 2 行），能力不丢。

## 1. P0-1：Plan/Build 模式持久化失效（真 bug）

**根因**：`stores/mode.ts:46-56` 依赖 `window.__srModeSession` 回调拿会话 id 才能写 `sr-mode-{sessionId}`，而该回调**生产代码零调用方**（仅 `test/preferenceStores.test.ts:94` 手动调用）→ `persist()` 永不执行 → 切会话后模式回默认值。测试绿是因为测试自己喂了钩子。

**修法（选 A：让 store 自持 sessionId，删掉 window 钩子）**

- `stores/mode.ts`：
  - 新增 `sessionId: string | null` 状态字段 + `bindSession(sessionId: string | null)`
  - `toggleMode()` 后由 store 内部用 `sessionId` 直接 `persist()`（不再靠 subscribe + window 钩子）
  - `loadSessionMode(sessionId)` 同时 `bindSession(sessionId)`，并把该 session 的持久化模式写入 store
  - **删除** `window.__srModeSession` 全局钩子与其 subscribe 持久化 workaround（问题根源）
- `components/chat/Composer.tsx`：在已有的 `loadSessionMode(currentSessionId)` effect 旁调用 `bindSession(currentSessionId)`（`loadSessionMode` 内已绑定则可省）
- 选 A 而非「补 `window.__srModeSession` 调用」的理由：全局 window 钩子是隐式契约，跨会话/多实例（编排面板自带 Provider）会写错会话 id；store 自持状态是显式数据流，且与 `persona`/`model` 两个 store 的既有做法一致（它们按 `sessionId` 参数持久化）。

**验收**
- 实机：会话 A 切 Plan → 切到会话 B 仍 Build → 切回会话 A 仍 Plan（`localStorage['sr-mode-{A}']==='plan'`）
- 编排会话（`dag:` 前缀）不污染普通会话的 mode 键
- 新增单测：`mode.ts` 的 `bindSession` + 切换 → 断言 `sr-mode-{id}` 被写入；且**不再依赖 window 钩子**（删掉旧测试里的 `__srModeSession` 调用）

## 2. P0-2：角色下拉污染（裸 id / 编排助手 / 顺序不稳）

**根因**：`api/routers/chat.py:1009-1024` `list_personas` 直接返回 `list_for(PromptBuilderFactory.list_roles())`，而 `list_roles()`（`kernel/agent/prompt/builder.py:507`）用 `set()` 合并全部已注册角色（含 `apps/agent_plugins/quant.py` 注册的内部执行角色 planner/evaluator/study_reviewer/study_collector），**无过滤、无稳定顺序**；`apps/chat/personas.py` 的 `PERSONA_LABELS` 只登记了 12 个面向用户的角色，注释还声称 `workflow_orchestrator` 不应出现在通用选择器——但从未被实现。

**修法（后端过滤，前端零改动）**

- `apps/chat/personas.py`：
  - 新增 `PUBLIC_PERSONA_IDS`（有序元组）= `PERSONA_LABELS` 中除 `workflow_orchestrator` 外的 11 个（chat / researcher / strategist / factor_analyst / data_quality / portfolio_construction / risk_controller / attribution_analyst / anti_overfit_analyst / backtest_diagnostics / critic）
  - 新增 `list_public(role_ids)`：**只返回** `role_ids ∩ PUBLIC_PERSONA_IDS` 中**确实已注册**的角色，按 `PUBLIC_PERSONA_IDS` 的固定顺序输出（稳定）
- `api/routers/chat.py::list_personas` 改调 `list_public(PromptBuilderFactory.list_roles())`
  - 未注册的公开角色（registry 里没有）自然不出现——保留「以 registry 为准」的原则，同时**固定展示顺序**
- 内部执行角色（planner/evaluator/study_reviewer/study_collector/orchestrator）**不出现在选择器**——它们是 DAG/研究流程内部角色，由 workflow 节点显式指定 role（`node_types.ts:331,443`），不该被用户当聊天助手选
- `workflow_orchestrator` 排除：与 `orchestrator-chat-design.md:17` 的既有裁决（编排会话固定用它）+ `ComposerToolbar.tsx:54` 已有的 `dag:` 会话锁定逻辑一致

**验收**
- `GET /api/chat/personas` 返回恰好 11 项，顺序固定（chat 第一），无裸 id，无「编排助手」
- 实机下拉：11 项全部有中文名 + 一句话说明
- 新增后端单测：`list_public` 的过滤 + 顺序 + 未注册角色不出现
- 编排会话（`dag:`）仍固定显示「编排助手」（前端既有逻辑不动）

## 3. P1-3：列表/引用类格式化在行中/多行场景插错

**现状**：`applyMarkdown`（`Composer.tsx:260-280`）无条件在光标处插入前缀 → 光标在行中得 `abc - def`；多行选中只给首行加前缀。

**修法**
- `applyMarkdown` 增加「行前缀型」语义（prefix 型 vs 包裹型）：
  - 包裹型（`**`/`_`/`` ` ``/` ``` `/`[]()`）：保持现状（实测正确）
  - 前缀型（`- ` / `1. ` / `> `）：定位到光标所在**整行行首**插入前缀；多行选中时**每行**都插入
- 前缀型统一映射（同时供快捷键使用）：
  - `Ctrl/Cmd+Shift+7` → 有序列表 `1. `（逐行递增编号）
  - `Ctrl/Cmd+Shift+8` → 无序列表 `- `
  - `Ctrl/Cmd+Shift+.` → 引用 `> `
- 包裹型快捷键：`Ctrl/Cmd+B` 粗体、`Ctrl/Cmd+I` 斜体、`Ctrl/Cmd+E` 行内代码

**验收**：实机覆盖 3 组（行首/行中/多行选中）× 3 种前缀型，结果均为整行/逐行正确；包裹型行为不变。

## 4. P1-4：去掉 8 个常驻格式化图标 + 补快捷键

**改动**
- `components/chat/ComposerToolbar.tsx`：删除 8 个 Markdown 按钮 + 分隔线；**只保留 persona 选择器**
  - 该组件因此退化为「角色选择器」，可更名为 `PersonaSelector.tsx`（重命名会牵动 import 与 mock，需同步 `composer.test.tsx:22` 的 mock）——**本批先不改名**，避免额外 churn
- `Composer.tsx`：保留 `applyMarkdown`（供快捷键调用）；新增键盘处理
- 快捷键**仅在输入框聚焦时生效**，且与 `useKeyboardShortcuts` 的全局绑定协调：
  - `Cmd/Ctrl+B` 在输入框内 = 粗体；不在输入框 = 现有「切换右侧面板」（保持）
  - 实现位置：Composer 的 `onKeyDown`（`Composer.tsx:396` 已有 `handleKeyDown`）——输入框内的按键不冒泡到全局 handler（需核对 `useKeyboardShortcuts` 是否过滤输入区目标；实测 `Cmd+B` 在输入框内无反应，说明全局已抑制，Composer 侧处理即可）
- 无选区时：包裹型快捷键不插入空标记（避免 `****` 这类噪音），前缀型仍可插入行首

**验收**
- 输入框上方只剩「角色选择器」1 个控件 + 状态行（Build/模型/思考/用量）
- 375 宽下整行从 4 行降到 2 行
- 选中文字按 `Cmd/Ctrl+B` → 变粗体；按 `Cmd/Ctrl+Shift+8` → 变列表；焦点不在输入框时 `Cmd+B` 仍切换右栏
- 现有 `composer.test.tsx`（ComposerToolbar 被 mock）不受影响；新增快捷键用例

## 5. 批次与顺序

| 批 | 内容 | 验证 |
|---|---|---|
| **F0** | P0-1 mode 持久化修复（store 自持 sessionId，删 window 钩子） | 新增单测 + 实机会话往返 + 全量 |
| **F1** | P0-2 personas 过滤（后端 `list_public` + 稳定顺序） | 新增后端单测 + 实机 11 项 + 全量 |
| **F2** | P1-3 `applyMarkdown` 行首/多行语义修正 | 新增单测（3 场景 × 3 前缀型）+ 实机 |
| **F3** | P1-4 移除 8 图标 + 补快捷键 | 新增快捷键用例 + 实机双主题双宽度 + 视觉基线重录 + 全量 |

依赖：F3 依赖 F2（快捷键复用修正后的 applyMarkdown）。F0/F1 相互独立，可先做。

## 6. 验收标准

| 断言 | 前 | 后 |
|---|---|---|
| Plan/Build 跨会话保持 | ✗ 永不持久化 | ✓ 按会话持久化且不串会话 |
| 角色下拉项数 | 19（含 5 裸 id、含编排助手） | 11，全部中文名+说明，顺序固定 |
| 角色顺序 | `set()` 合并，不稳定 | 固定顺序（chat 在首） |
| 输入框上方控件 | 9（助手+8 图标） | 1（助手） |
| 375 宽工具行 | 4 行 | 2 行 |
| 前缀型格式化（行中/多行） | `abc - def` / 仅首行加前缀 | 整行/逐行正确 |
| 格式化快捷键 | 无 | Cmd+B/I/E、Cmd+Shift+7/8/. |
| 全量验证 | — | 前端全量 / tsc / lint、后端全量、gate 13/13、双主题基线重录、实机双宽度 |

## 7. 不做

- 不删 `applyMarkdown` 能力本身（仅去掉按钮入口，改由快捷键提供）
- 不改 persona 选择器的交互形态与位置（仅内容变干净）
- 不动 `agent_id`/`mode`/`thinking`/`model` 的后端契约
- 不给内部执行角色（planner/evaluator 等）做用户可见入口
- 不改 `ComposerToolbar` 文件名（避免额外改名 churn）
- 不 push（本地提交）

## 8. 风险与回滚

- **F0**：mode 存储键不变（`sr-mode-{sessionId}`），回滚无迁移成本；但删 window 钩子会改 `preferenceStores.test.ts`（旧测试自己喂钩子）——**该测试需按新契约重写为「bindSession 后切换即持久化」**，属行为断言而非实现断言。
- **F2**：`applyMarkdown` 行为变更可能影响依赖旧行为的测试——需先扫现有断言（当前 `composer.test.tsx` 未直接断言格式化行为，风险低）。
- **F3**：视觉基线会变（工具行少了 8 个图标）→ 需重录双主题相关基线；`catalog` 的 markdown story 基线**不受影响**（那是组件故事，不含 composer 工具行）。
- 每批独立提交，可单独 `git revert`。

## 9. 实施记录（2026-11-24，用户「批准」后执行）

| 批 | 实际落地 | 偏差 |
|---|---|---|
| **F0** | `stores/mode.ts` 自持 `sessionId`，`setMode`/`toggleMode` 内直接 persist，删除 `window.__srModeSession` 钩子与 subscribe workaround；`loadSessionMode` 同时绑定 session。测试从「喂 window 钩子」改为行为断言（绑定后持久化 / 切换持久化 / 未绑定不写 / 会话间不串），新增 4 例。实机：切 Plan→新建会话 Build→切回 Plan 往返保持，`sr-mode-{id}=plan` 真的写入。 | 无——Composer 侧无需改动，`loadSessionMode(currentSessionId)` 已在 `Composer.tsx:70` 调用 |
| **F1** | `apps/chat/personas.py` 新增 `PUBLIC_PERSONA_IDS`（11 项有序）+ `list_public()`；`chat.py::list_personas` 改调 `list_public`。新增后端测试 8 例。实机 API 与下拉验证：19 项 → 11 项，全部中文名+说明，无内部角色，chat 首位。 | 无 |
| **F2** | 新增 `utils/markdownShortcuts.ts` 纯函数模块（`wrapSelection` / `applyLinePrefix`）+ 同名测试 14 例；Composer 改为调用纯函数。行前缀型：前缀落整行行首、多行选中逐行加、有序列表递增编号、选区终点恰在行首时该行不算选中。 | 纯函数独立成模块而非留在组件内——便于直接单测边界，Composer 与快捷键共用同一语义 |
| **F3** | `ComposerToolbar` 移除 8 个 Markdown 按钮与 `onApplyMarkdown` prop（组件退化为纯角色选择器）；Composer 加 `Cmd/Ctrl+B/I/E`（包裹型，须有选区）与 `Cmd/Ctrl+Shift+7/8/.`（行前缀型，无选区亦生效）。新增集成用例 9 例。实机：1280 宽 9 控件 2 行 → **4 控件 2 行**；375 宽 4 行 → **3 行**；5 个快捷键行为全部正确。 | 快捷键行前缀型按 **`e.code`（物理键位）**判定而非 `e.key`——Shift+8 的 `e.key` 是 `*`、Shift+7 是 `&`（美式键盘），按 key 匹配会静默失效（实机首次验证即发现） |

### 验收结果

| 断言 | 目标 | 实际 |
|---|---|---|
| Plan/Build 跨会话保持 | ✓ | ✓ 实机往返验证 |
| 角色下拉项数 | 11，全中文名，顺序固定 | ✓ 11 项，chat 首位 |
| 输入框上方控件 | 9 → 1(+状态行) | ✓ 4 个（助手 + Build/模型/思考/用量） |
| 375 宽工具行 | 4 行 → 2 行 | ✓ 3 行（助手 / Build+模型 / 思考） |
| 前缀型格式化（行中/多行） | 整行/逐行正确 | ✓ `- abc def`、`- a\n- b\n- c` |
| 格式化快捷键 | 5 个 | ✓ 全部生效 |
| 前端 | 全量 / tsc / lint | 1394 passed / OK / 0 error |
| e2e | 114 / 0 skip | ✓ 114 passed（工具行不在基线场景内，无需重录） |
| 后端 | 全量 | 8152 passed / 185 skipped / 2 xfailed |
| gate | 13/13 | ✓ ALL GREEN |

### 过程中的两处实测发现

1. **架构体量数字漂移**：F1 改 `chat.py` 与 `personas.py` 触发 `test_framework_numbers_real_repo_consistent` / `test_architecture_diagram_matches_code` 失败 → 跑 `sync_architecture_diagram.py --write` + 同步 `framework-and-principles-v1.2.md` 与 `框架与理念 · v1.2.html`（L1 22,104 / L2 11,713 / total 97,536；跨层边 L1→L2 76 · L1→L3 139 · L1→L4 71 · L2→L3 49 · L2→L4 14 · L3→L4 93）。
2. **IAB 后端不派发真实按键事件**：浏览器实机验证快捷键时 `tab.playwright.press()` 与 `cua.keypress` 均无事件到达（window 级捕获器也收不到），属**验证环境限制**而非代码缺陷；改用页面内派发 `KeyboardEvent` 在真实页面代码上验证 5 个快捷键行为全部正确（jsdom 单测另有一份独立验证）。
