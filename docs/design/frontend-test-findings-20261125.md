# 前端页面完整测试记录（2026-11-25）

> 合并模式（后端单端口 8766 托管构建版前端），浏览器 1280×720，
> 基于用户手动启动的服务。测试方式：真实 GUI 操作 + DOM 只读观察 +
> 截图留证；不注入脚本改页面状态。

## 环境

- 后端：`python -m agentnodes.interfaces.cli webui serve --host 127.0.0.1 --port 8766`
- 页面：`http://127.0.0.1:8766/chat`
- 截图：`gui-test-screenshots/fe_full_test_chat.png`
- 注：测试期间当前模型为 `minimax/MiniMax-M3.1-Flash`，persona 从「通用助手」切到「研究员」（已切回默认不在服务端持久化）。

## 测试结果总览

| 级别 | 测试点 | 结果 | 证据 |
|---|---|---|---|
| P0 | 合并模式页面加载 | 通过 | 登录页正常渲染，DOM 快照 |
| P0 | 登录（admin） | 通过 | 登录后进入会话工作台 |
| P1 | 会话列表/选中/历史消息加载 | 通过 | 选中「回复一个字：好」后 4 条消息加载完整 |
| P1 | SSE 连接状态 | 通过 | Banner 显示「已连接」 |
| P1 | 消息收发 | 通过 | 发送「测试：只回复 OK 两个字」→ 回复「OK」，输入清空 |
| P2 | 模型选择器 | 通过 | 展开显示 4 个可用 + 8 个禁用（未配置密钥） |
| P2 | Persona 选择器 | 通过 | 11 个 persona，切换到「研究员」生效 |
| P2 | Markdown 快捷键 Ctrl+B | 通过 | 选中「加粗测试」→ `**加粗测试**` |
| P2 | 用量圆环 hover 弹层 | 通过 | 7 段明细可见；hover 后 `invisible` 类移除 |
| P2 | 会话搜索 | 通过 | 搜索「你好」只匹配对应会话 |
| P2 | 星标/归档过滤 | 通过 | 无数据时显示「暂无会话」 |
| P3 | 布局溢出 | 通过 | scrollW=clientW=1280，scrollH=clientH=720 |
| P3 | 主题切换（深/浅） | 通过 | 切换后按钮状态翻转，已恢复深色 |
| P3 | 右侧面板隐藏/显示 | 通过 | 按钮状态翻转，无溢出，已恢复 |

## 发现的问题（待讨论）

### 问题 1 · 上下文用量两处数值口径不一致（中）

**现象**：状态栏显示 `~7 / 1.0M (0.0%)`，但 hover 弹层 7 段明细显示
「历史消息 2 (0%) / 系统提示 2,374 (100%) / 其余 0」。

**已核实**：
- 状态栏走 `contextUsage.ts` 的 `estimateTokensFromMessages()`，只统计前端已加载
  消息的 parts 文本（chars/3.5）→ 得 7
- 弹层走后端 `context.breakdown` 事件，统计发给 LLM 的完整 messages + tools
  schema（chars/3.0）→ 系统提示 2374
- `BreakdownList` 里的 `(100%)` 是「占分段总和的比例」，口径本身正确

**影响**：用户会看到「总量 7」与明细「2374」自相矛盾，无法理解哪个才是真实上下文占用。

**候选方向**（未实施，待裁决）：
1. 状态栏与弹层统一数据源：都用 `context.breakdown` 的分段总和作为「已用 token」
2. 保持两源，但在 UI 明确标注口径差异（如状态栏标注「消息文本估算」，弹层标注「含系统提示/工具 schema 的 LLM 上下文估算」）
3. 让 `estimateTokensFromMessages` 也计入 system prompt/tools——但前端拿不到完整 system prompt，不可行

**建议**：方向 1（统一数据源）最简洁，且后端单一事实源已具备；需确认 `context.breakdown` 在空闲会话也会 emit（当前只在 `llm_request` 装配点 emit，空闲会话没有该事件 → 需设计首次加载的明细来源）。

### ~~问题 2 · 输入框草稿跨会话残留~~ — 误判，已撤回

**复核结论**：不是 bug。`Composer.tsx:63-86` 用 `sr-draft-${sessionId}` 按会话隔离草稿，
切换会话时会恢复各自草稿（`composer.test.tsx:215-233` 有对应契约测试）。
搜索/过滤不改变 sessionId，草稿保留是正确行为。

### 问题 3 · 会话过滤与搜索叠加无结果时无提示（低）— 已修复

**现象**：搜索框填「你好」后切到「星标」过滤 → 只显示笼统的「暂无会话」，
用户不知道是搜索词还是过滤条件导致的空结果。

**修复**（`SessionSidebar.tsx` + `sessionSidebar.test.tsx`）：空状态分三种精准提示：
- 有搜索词 → 「没有匹配「xxx」的会话」+「清除搜索」按钮
- 有过滤（星标/归档）→ 「暂无星标/归档会话」+「查看全部」按钮
- 均无 → 保持「暂无会话」

**验证**：受影响测试 18 passed；全量前端 1434 passed；tsc 0 error；
真机（重建静态产物后）确认搜索空结果提示生效。

### 问题 4 · 账户页「个人账号」标题重复（中）— 补测发现，已修复

**现象**：`账户与设置` 页连续出现两个 `<h3>个人账号</h3>`。

**证据**：`evaluate` 读到 7 个 h3：`LLM 配置 / 个人账号 / 个人账号 / 工作区设置 /
聊天布局 / 外观设置 / 用户管理`。

**根因（代码已核实）**：
- `webui/frontend/src/pages/AccountPage.tsx:61` 用 `<Section icon={SettingsIcon} title="个人账号">`
  包裹 `<AccountSection />`
- `webui/frontend/src/components/settings/AccountSection.tsx:54` 自身又有
  `<Section icon={User} title="个人账号">`
- 两层 Section 同名 → 标题渲染两次（account merge 重构 R-014 引入的回归）

**修复**：去掉 `AccountPage.tsx:61-63` 的外层 Section 包装，直接渲染 `<AccountSection />`
（内层 Section 自带 User 图标与标题，是组件自身契约）。
`accountPage.test.tsx` 的 AccountSection mock 改为渲染 `<h3>` 并增加
`heading 名称=个人账号` 恰好 1 个的断言防回归。

**验证**：受影响测试 3 passed；全量前端 1434 passed；tsc 0 error；重建静态产物后
真机 H3 列表从 `[…, 个人账号, 个人账号, …]` 变为单个「个人账号」。

## 补测结果（Trace / 账户与设置 / 窄屏）

| 测试点 | 结果 | 证据 |
|---|---|---|
| Trace 页加载 | 通过 | 会话下拉 2 项、迭代筛选、事件类型筛选、时间线渲染 |
| Trace 会话切换 | 通过 | 切到「你好，你能做什么」→ 9/9 事件（含历史 2056 错误） |
| Trace 事件类型筛选 | 通过 | 点「错误」→ 1/1 事件（Playwright click 超时，DOM CUA 点击成功） |
| 账户页加载 | 通过（含问题 4） | LLM 配置 / 个人账号 / 工作区 / 聊天布局 / 外观 / 用户管理均渲染 |
| 聊天布局切换 | 通过 | 气泡式 ↔ 扁平式，选中边框切到 `border-primary-500`；已恢复 |
| 窄屏 375×812 · Chat | 通过 | scrollW=clientW=375，21 个交互元素可见，无越界元素 |
| 窄屏 375×812 · 账户页 | 通过 | scrollW=clientW=375，无横向溢出 |
| 视口恢复 | 通过 | 已恢复 1280×720 |

**工具层观察（非产品 bug）**：`Playwright getByRole(...).click()` 在 Trace「错误」按钮和
导航链接上超时（元素存在、count=1、visible），DOM CUA `click({node_id})` 可点击。
可能是透明覆盖层拦截 pointer events，建议后续排查；本次不据此判定产品缺陷。

## 悬浮窗视觉专项（2026-11-25 补测，发现并修复问题 5）

| 测试点 | 结果 | 证据 |
|---|---|---|
| hover 展开 | 通过 | `visibility: visible`，opacity=1，64px 大环 + 7 段明细均渲染 |
| 深色主题背景 | **原为 bug，已修复** | 修复前 `backgroundColor: rgba(0,0,0,0)`（完全透明）；修复后 `rgba(255,255,255,0.043)` |
| 浅色主题背景 | 通过 | `rgb(255,255,255)` |
| 文字对比度 | 通过（余量小） | 深色数值 `#a3a3a3` on ≈`#303030` ≈6.2:1；浅色 `#737373` on 白 ≈4.6:1（刚达 AA） |
| 字号 | 偏小（观察项） | 明细行 / 页脚均为 `10px`，标题 `11px` |
| 视口边界 | 通过 | 弹层 288×284 位于 (661,376)-(949,660)，未超出 1280×720 |
| 截图证据 | 已保存 | `gui-test-screenshots/popover_after_fix_dark.png` |

**问题 5 · `.glass` 背景变量缺失（中）— 已修复**

根因：`globals.css` 的 `.glass` 用 `background: var(--surface)`，但两个主题块都未定义
`--surface`（只有 `--card` / `--popover`）→ 所有纯 `.glass` 弹层背景完全透明，
仅靠 backdrop-blur 勉强可读。这正是"感觉看不清"的根因。

修复：dark 加 `--surface: rgba(255,255,255,0.045)`，light 加 `--surface: #ffffff`
（对齐已有 `--card`/`--popover` 值）。有显式 `bg-*` Tailwind 类的组件不受影响
（实测 Composer 的 `bg-slate-900/40` 仍生效）。

验证：全量前端 1442 passed；真机深浅两主题背景色均正确；弹层未越界。

## 未验证项

- 业务 `error` 事件的浏览器误断路径（黑盒无法可靠受控触发）
- 长消息/大数据量下的渲染性能
- 窄屏截图（screenshot 在 375×812 下超时 30s，未能留证；几何检查已通过）
- 截图因当前模型不支持图片输入，**未做人工目视确认**，视觉结论均来自计算样式/几何只读检查

## 建议下一步

1. 排查 Playwright click 超时的覆盖层问题
2. 如需进一步提升可读性：明细行字号 10px → 11px（浅色主题对比度余量偏小）
3. 补窄屏人工目视确认（当前模型不支持图片，需人工打开截图）
