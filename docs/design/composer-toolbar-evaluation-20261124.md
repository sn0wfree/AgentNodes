# 输入框工具行评估（2026-11-24 · 零代码 · 评估记录）

> 状态：**评估记录**，不改任何代码。涉及修复的项均需另出 R-014 方案并批准。
> 对象：输入框上方整行 = `ComposerToolbar`（助手选择器 + 8 个 Markdown 格式化按钮）+ `ComposerStatusBar`（Build/Plan、模型、思考模式、上下文用量）。
> 方法：读代码 + 浏览器实机逐项操作（1280 / 375 双宽度）+ 后端契约核实 + 对照业界与既有设计文档。

## 1. 这行由什么组成（实测）

| 元素 | 实现 | 后端是否真消费 | 持久化 |
|---|---|---|---|
| **通用助手**（persona 选择器） | `ComposerToolbar.tsx:52-105` → `GET /api/chat/personas` | ✅ `send_async` 传 `agent_id` → `_resolve_loop_prompt(None, agent_id)` 决定 system prompt 与角色 | ✅ 按会话 `sr-persona-{sessionId}` |
| **粗体/斜体/行内代码/代码块/无序列表/有序列表/引用/链接**（8 个） | `Composer.tsx:260-280` `applyMarkdown` | ❌ 纯前端文本插入 | ✅ 草稿 `sr-draft-{sessionId}` |
| **Build / Plan**（模式） | `ComposerStatusBar.tsx:22-25` | ✅ `send_async` 传 `mode` → `_shell_tools_enabled(mode)` 决定 shell 工具开关 | ❌ **实际不持久化**（见 §4.1） |
| **模型选择** | `ComposerStatusBar.tsx` → `GET /system/llm` | ✅ `send_async` 传 `model` | ✅ `sr-session-models` |
| **思考模式**（Auto/On/Off） | `ComposerStatusBar.tsx` → `thinkingPref` | ✅ `send_async` 传 `thinking` → `_thinking_instructions` | ✅ `sr-thinking-mode`（全局） |
| **上下文用量**（0% / 1000K） | `ComposerStatusBar.tsx:193-210` | ✅ 本地 `tokensUsed` / `modelInfo.context_tokens` | — |

**结论：这一行没有装饰性元素**，6 类控件全部对接真实后端契约。

## 2. 排布（实测）

- **1280 宽**：2 行 —— 行1「助手 + 8 个格式化图标」，行2「Build / 模型 / 思考 / 用量」。
- **375 宽**：**4 行** —— 行1「助手 + 前 5 个格式化图标」，行2「后 3 个格式化图标」，行3「Build / 模型」，行4「思考 / 用量」。

格式化按钮独占 2 行、在 375 宽下把整行撑到 4 行，是这行最突出的视觉负担。

## 3. 与业界/既有设计的对照

| 项 | 业界通行 | AgentNodes 现状 | 评价 |
|---|---|---|---|
| 输入框上方放模式/模型/思考 | ChatGPT / Claude / Cursor 都在底部 composer 内，**同一条 bar** | 一致 | ✅ 合理 |
| Markdown 格式化按钮常驻在输入框上方 | **罕见**。ChatGPT/Claude 无；Cursor/VS Code 用**选中时浮出**的上下文工具条；Notion 用 `/` 命令 | **8 个图标常驻**，占满一整行 | ⚠️ 密度过高，且缺替代路径（无快捷键、无 `/` 命令） |
| persona/角色选择器 | Claude Code 有 subagent 选择、Cursor 有 mode | 有，且 19 个角色 | ✅ 方向合理，但**清单质量有问题**（§4.2） |
| 上下文用量贴输入框 | 一致 | 一致 | ✅ |
| Build/Plan 双模式 | Cursor/Claude Code 有（Plan 只读） | 有 | ✅ 但持久化坏了（§4.1） |

## 4. 实证问题（4 项）

### 4.1 Plan/Build 模式**从不持久化**（功能不工作）

`stores/mode.ts:46-56` 用 `window.__srModeSession` 接收会话 id 才能写入 `sr-mode-{sessionId}`，但**生产代码零调用方**——只有 `test/preferenceStores.test.ts:94` 手动调它。实测：切到 Plan 后 localStorage 无 `sr-mode-*` 键，切回会话仍回到 Build。

测试是绿的（它自己喂了 `__srModeSession`），功能却是坏的——典型的「测试钉住实现细节而非行为」。

### 4.2 角色下拉混入内部/裸 id 角色

实测下拉 19 项，其中：
- **5 项只有裸 id、无中文名与说明**：`study_reviewer`、`study_collector`、`evaluator`、`orchestrator`、`planner`（来自 `apps/agent_plugins/quant.py` 的插件注册，无 `PERSONA_LABELS` 登记）
- **`编排助手`（workflow_orchestrator）出现在通用选择器里**，与 `apps/chat/personas.py:15-16` 注释「workflow_orchestrator 不出现在通用选择器里」**直接矛盾**
- 顺序不稳定：后端 `list_roles()` 用 `set()` 合并（`kernel/agent/prompt/builder.py:507`），每次进程启动顺序可能不同
- `archive/undated/orchestrator-chat-design.md:17` 的既有裁决「persona 选择器在编排 session 锁定为 workflow_orchestrator（保留按钮置灰）」**从未在后端实现**

### 4.3 列表/引用按钮在光标位于行中时插错位置

`applyMarkdown` 无条件在光标处插入前缀，实测：

| 场景 | 输入 | 结果 | 应为 |
|---|---|---|---|
| 光标在行中 | `abc def`（光标在 def 前）+ 无序列表 | `abc - def` | `- abc def` |
| 光标在行中 | `abc def` + 引用 | `abc > def` | `> abc def` |
| 多行选中 | `a\nb\nc` 全选 + 无序列表 | `- a\nb\nc` | 每行都加前缀 |

粗体/斜体/代码等**包裹型**按钮无此问题（实测正确）；仅**前缀型**（列表/引用）受影响。

### 4.4 格式化能力无快捷键、无 `/` 命令替代

8 个格式化按钮是纯鼠标操作。实测在输入框内按 `Ctrl+B` **无任何反应**（不粗体、右栏也不切换——全局 `Cmd+B` 已被「切换右侧面板」占用并在输入区被抑制）。用户想要「加粗」必须精确点中那个小图标。

## 5. 结论与建议（按优先级）

| 优先级 | 结论 | 建议方向 |
|---|---|---|
| **P0** | 4.1 Plan/Build 持久化是**真 bug**（功能承诺存在但不工作） | 修 `mode.ts` 的会话 id 传递（Composer 挂载时调用），或让 mode store 直接持有 sessionId |
| **P0** | 4.2 角色清单污染（裸 id + 编排助手 + 顺序不稳） | 后端 `list_personas` 过滤：只返回 `PERSONA_LABELS` 登记项、排除 `workflow_orchestrator`、按登记顺序返回；补齐或隐藏裸 id 插件角色 |
| **P1** | 4.3 前缀型按钮行中插错 | `applyMarkdown` 感知行首：对所在行整行加前缀，多行选中则每行加 |
| **P1** | 4.4 格式化无快捷键 | 补 `Ctrl/Cmd+B/I/E` 等标准快捷键（输入框内，且不与全局冲突）；或并入 `/` 命令面板 |
| **P2** | 格式化 8 图标常驻，375 下占 2 行、整行 4 行 | 业界做法是**选中时浮出**上下文工具条；或折叠为「更多」；或至少在窄屏隐藏低频项（引用/链接） |
| **P2** | 组件分裂为两行两组件（ComposerToolbar / ComposerStatusBar），行高与间距各自为政 | 合并为单一 composer 底栏，或至少统一间距/分隔符规格 |
| — | 6 类控件均为真功能 | 保留，不删 |

## 6. 明确不做（本次评估边界）

- 不改任何代码、不改任何视觉
- 不删格式化按钮或角色选择器（业界对照不支持删，只支持重构呈现方式）
- 不动 `agent_id`/`mode`/`thinking`/`model` 的后端契约（均已正常工作）
- 4.2 的「裸 id 插件角色是否应出现在选择器」属产品决策：建议**隐藏**（内部执行角色），但需用户裁决

## 7. 来源

- 代码：`ComposerToolbar.tsx`、`ComposerStatusBar.tsx`、`Composer.tsx:260-280`、`stores/mode.ts`、`stores/personas.ts`、`stores/thinkingPref.ts`
- 后端：`api/routers/chat.py:1009-1024`（`/personas`）、`:166-244`（`send_async` 消费 agent_id/mode/thinking/model）、`apps/chat/personas.py`、`kernel/agent/prompt/builder.py:507`
- 实机：127.0.0.1:5173 双主题双宽度操作记录
- 既有文档：`orchestrator-chat-design.md:17`（编排 persona 锁定裁决）、`project-architecture-review-and-refactor-plan-20261118.md:53`（persona 归 apps/chat）
