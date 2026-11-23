# 侧边栏设计业界调研（图标 rail · 会话栏 · 右侧上下文面板）

> 日期：2026-11-23
> 性质：设计调研；为三栏（左图标导航 / 会话侧栏 / 右侧上下文面板）的取舍提供业界证据。
> 关联：[U-A 前端实现计划](./ui-ua-frontend-execution-plan-20261122.md) §3.1/§3.2、
> [U-A 复用评估](./ui-ua-reuse-assessment-20261122.md)、示意图 `tmp/mockup/agentnodes-ui-mockup.html`（sidebar 232px + 右工作台）。

## 1. 调研范围与方法

三个被调研面（对应本应用现状）：

| 面 | 本应用现状 |
|---|---|
| ① 导航 rail（最左图标条） | 6 项：AN 首页 · Chat · 编排 · 用户管理(管理员) · 设置 · 登出（2026-11-23 已从 9 项精简，6c27a72e） |
| ② 会话侧栏（232px 列表） | 新建 + 搜索框 + 全部/星标/归档过滤 + 时间分组（今天）+ hover 动作（星标/改名/归档/删除） |
| ③ 右侧上下文面板 | 常驻四卡：TOKEN 使用情况 / 目标&进度 / 表现曲线 / Trace Timeline（新会话下三张为空占位） |

产品域：封闭 AI chat（ChatGPT / Claude）、自托管（LobeChat / Open WebUI / LibreChat）、
IDE 系（Cursor / VS Code Copilot Chat）、设计规范（Material 3 Navigation Rail）。
方法：2026-11-23 web 检索 + 产品既有知识综合；来源见 §6。

## 2. 逐产品表现

| 产品 | ① 导航 | ② 会话/列表栏 | ③ 右侧面板 |
|---|---|---|---|
| **ChatGPT** | 无独立 rail——一切收进左侧单栏（新聊天 / 搜索 / Library(GPTs) / Projects） | 会话按时间分组、hover 显动作；**2025 改版把 pinned/GPTs 收起引发社区反感**；Projects 默认折叠 +「查看更多」被持续诟病 | 无常驻右栏；Canvas **按需**弹出成对写作/代码的并列面板 |
| **Claude** | 左栏含 Projects 与 Artifacts 专门分区（2025-06 起） | 项目 = 会话的工作空间分组 | **Artifacts 右栏**：创作物（文档/代码/应用）是右栏第一公民；与左侧入口联动 |
| **LobeChat** | 左栏三分区：会话 / 助手 / 市场 | 助手为中心的列表组织 | 插件与助手设置走右侧抽屉（按需） |
| **Open WebUI** | ChatGPT 式左栏 | 历史 + 文件夹 + 模型选择 | 参数面板按需 |
| **LibreChat** | 务实多会话栏 | endpoint（多供应商）切换嵌在会话栏 | — |
| **Cursor** | 左侧文件树为主 | — | **右侧专用 AI 面板（首创此范式）**；社区长期投诉：面板与底部终端/问题面板互相挤压 |
| **VS Code Copilot Chat** | 学 Cursor：默认**右侧次要边栏**，可自定义搬回左侧 | — | 官方把「加上下文 / 改请求」定义为面板核心交互 |
| **M3 规范** | Navigation Rail：**3–7 个目的地** + 可选 FAB；中型屏组件（窄→底栏、宽→drawer）；2025 新增 **collapsed/expanded 两态**官方规范 | — | — |

## 3. 模式提炼

- **P1 rail 条目 3–7、底部锚定**：主导航 3–7 项（M3），设置/登出等低频动作永远沉底——与我们精简后的 6 项一致。
- **P2 rail 两态可折叠**：collapsed（纯图标）/ expanded（图标+文字）已是 M3 官方规范；适合后续批次（P1-1 令牌批）。
- **P3 会话列表：时间分组 + hover 才显动作**：ChatGPT/Open WebUI 均按 今天/昨天/7 天内 分组；动作（星标/改名/归档/删除）hover 才出现。我们已有 今天 分组 + hover 动作，分组粒度可细化。
- **P4 分组默认展开**：ChatGPT Projects 的「默认折叠 + 查看更多」是长期社区痛点——分组宁可展开。
- **P5 右栏 = 当前会话的上下文，不是全功能入口**：Claude Artifacts、Copilot Chat 的右栏内容随会话而有/无；没人把全局功能塞进右栏常驻卡。
- **P6 空态诚实：无数据不渲染卡**：业界无「三张空占位卡常驻」的先例；空槽位要么隐藏要么收成一行可展开项（对照我们现状：TOKEN/目标/曲线 三卡在空会话下全是占位）。
- **P7 右栏可折叠 + 可拖宽**：Cursor/VS Code 均支持；我们已有 `rightRatio` 拖拽 + 隐藏按钮 ✓。
- **P8 窄屏面板互斥**：Cursor 社区长期投诉 AI 面板与底部面板挤压——窄屏互斥（我们 `max-md` 已做 absolute 叠加）是对的方向。
- **P9 token meter 贴近输入框 + 阈值变色**：消费端 context meter 已是成型模式（读当前模型、显示"多满"）；放输入框附近比藏在右侧卡更有用，阈值（如 70/90%）变色并给压缩建议。
- **P10 入口诚实性**：未上线/明示 mock 的 surface 不进导航——业界一致；与计划 §8.1 同源（图标精简 6c27a72e 依据此条）。

## 4. 对本应用三栏的映射建议

| 区域 | 现状 | 业界依据 | 建议 | 批次归属 |
|---|---|---|---|---|
| ① 图标 rail | 6 项 ✓ | P1（3–7 项）+ P10 | 已达标；后续可做 collapsed/expanded 两态折叠（P2） | P1-1 令牌批 |
| ② 会话侧栏 | 搜索+过滤+今天分组+hover 动作 | P3/P4 | 分组细化（昨天/7 天内/更早）；过滤 tab 保持展开 | P1-2 会话高级批 |
| ③ 右侧面板 | 四卡常驻、三张空占位 | **P5/P6（核心差距）** | 空卡不渲染：上下文/token meter 移到输入框附近（P9，带阈值变色）；目标卡仅在有活跃 goal 时出现；表现曲线仅在 run 产出后出现；Trace 保持一行入口（现状已对） | P1-3 右栏批 |
| ③ 窄屏 | absolute 叠加互斥 ✓ | P8 | 维持 | — |
| 反例 | MonitorPage 无订阅渲染 SSEStatus（假「已断开」） | P10/§3.2 | 移除或改「非会话页」文案 | follow-up（已登记计划 §12） |

## 5. 反模式对照（我们的问题 ↔ 业界印证）

| 我们的问题 | 业界印证 | 结论 |
|---|---|---|
| 空会话下右栏三张空占位卡堆叠 | 无业界先例（P6） | 改为按数据出现 |
| token 使用藏在右栏卡片 | P9：meter 贴输入框 + 阈值变色 | 迁移 |
| 监控页渲染不订阅的连接状态 | P10：不订阅不显示 | 移除/改文案 |
| 2025 ChatGPT 把常用入口收起引发反感 | P4：折叠偏好反弹 | 我们的过滤 tab 保持展开 ✓ |

## 6. 来源

- OpenAI 社区：[侧栏自定义请求](https://community.openai.com/t/fully-customizable-sidebar-navigation-and-workspace-organization-across-chatgpt/1415346/2)、[2025 侧栏改版争议](https://www.popularai.org/p/chatgpt-sidebar-pinned-chats-gpts-projects-missing)
- Claude：[Artifacts 官方文档](https://support.claude.com)、[2025-06 Artifacts 侧栏分区（Medium）](https://medium.com)
- 自托管对比：[elest.io：LobeChat vs Open WebUI vs LibreChat](https://blog.elest.io/the-best-open-source-chatgpt-interfaces-lobechat-vs-open-webui-vs-librechat)、[Open WebUI 官方对比页](https://docs.openwebui.com/alternatives/librechat)
- IDE 系：[VS Code Chat 官方文档](https://code.visualstudio.com/docs/chat/chat-overview)、[VS Code 自定义布局](https://code.visualstudio.com/docs/getstarted/userinterface#_custom-layout)、[Copilot Chat 右侧边栏迁移（Stack Overflow）](https://stackoverflow.com/questions/79162102/how-can-i-put-copilot-chat-back-in-the-main-sidebar)、[Cursor 布局反馈 megathread](https://forum.cursor.com)
- 规范：[M3 Navigation Rail](https://m3.material.io/components/navigation-rail/overview)（collapsed/expanded 两态）、[Compose Navigation Rail](https://developer.android.com/develop/ui/compose/components/navigation-rail)（3–7 目的地）
- Context meter：[为四个 AI 应用做 context meter（Level Up Coding）](https://levelup.gitconnected.com/i-built-a-context-meter-for-four-ai-chat-apps-counting-tokens-was-the-easy-part-f2c0f5afc26a)
