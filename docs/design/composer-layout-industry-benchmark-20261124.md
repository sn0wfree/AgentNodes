# 输入框（Composer）布局对标调研：ZCode / MimoCode / WorkBuddy（2026-11-24 · 零代码）

> 状态：**调研记录**，不改任何代码。用户诉求「借鉴一下 zcode 和 mimocode 还有 workbuddy 对话框的布局设定」。
> 结论先行：**ZCode 有一手 bundle 源码可精确对照**；MimoCode / WorkBuddy **没有公开的输入框布局资料**（仅确认产品身份，布局部分为标注的推断）。

---

## 1. ZCode composer（一手源码，可信度高）

**来源**：本机解包的 renderer bundle（v3.18.2）
- `tmp/probe/zcode_ui/out/renderer/assets/styles-a4AckxCl.js`（7.0MB，含全部 JSX 结构）
- 同包 `styles-D4aYlB9N.css`（492KB）
- 辅助：`docs/ui-zcode-source-research-20261114.md`（逆向概览）
- 用户截图 `382fddd7-...png`（1186×158，**OCR 失败**，仅像素旁证，低可信度）

### 1.1 容器形态

**一个大圆角容器包住全部内容**，不是工具条在框外：

```
data-composer-layout:
  relative flex flex-col gap-3 overflow-hidden rounded-2xl
  border-[0.5px] border-input-border bg-input p-3
  shadow-(--shadow-composer-soft)
  hover:border-input-border-hover
  focus-within:!border-input-border-focused focus-within:bg-input-focused
```

外层 `chat-composer-input-surface relative w-full rounded-2xl bg-surface`。CSS 侧 `highspeed-composer-surface` 明确 `border-radius:16px`。灰阶中性表面（`#2b2b2b` 类），不靠颜色强调。

### 1.2 内部行结构（核心 2 行，条件 3 行）

| 行 | 内容 |
|---|---|
| 第 0 行（条件） | `v4-composer-edit-bar`（编辑排队消息提示 + 取消按钮）+ 附件/上下文 chips 行 |
| 第 1 行 | Markdown 编辑器（contenteditable，支持 `@` 提及 / `/` 命令 / `$` 技能） |
| 第 2 行 | 工具条：左组 `data-composer-leading-actions`，右组 `data-composer-trailing-actions` |

浮层（不占行）：提及/斜杠面板锚在输入框上方 `absolute inset-x-0 bottom-full z-20`。

### 1.3 工具条左右两侧

**左侧**：`+` 更多（附加文件 / goal / workflow / 插件）、工作区/项目选择、模式选择（default/build/edit/plan/yolo/guarded）、后台工作、worktree、待审批入口、CUA 入口。窄宽度按优先级折叠。

**右侧**（顺序）：
1. **上下文用量触发器** `chat-context-usage-trigger` —— 低调图标，`available`=success 色、`urgent`=warning 底色
2. **模型选择器** —— provider + model + effort 区（popover `w-72`）
3. **推理档位滑杆** `composer-thought-slider` —— off/minimal/low/medium/high/xhigh/max/ultra；**"最高"是推理努力档，不是额度档**（额度在单独的 coding plan 体系）
4. **语音输入** `v4-composer-voice-input`（麦克风）
5. **发送按钮** `v4-composer-send` —— **圆形、品牌蓝实心、箭头图标**；运行中变圆形停止按钮

### 1.4 排队消息编辑（AgentNodes 缺）

`v4-composer-edit-bar` 在**容器内部顶部**：铅笔图标 + "正在编辑排队消息"文案 + 取消按钮；可重置对话和文件；应用编辑时确认模型/模式变更。队列条目侧有编辑/锁定/提示。

### 1.5 用量显示（与 AgentNodes 刚做的 F4-b 对照）

ZCode 是**细图标 + 向上弹层**（popover `w-80`）：
- 标题 + 等宽百分比
- **7 段分段进度条**（messages / system_prompt / meta_user_context / skills / tool_prompt / system_tool_schemas / mcp_tool_schemas）——不只是单个百分比
- 缓存命中率、coding plan 用量/重置/额度

AgentNodes F4-b 是**行内紧凑文本 + hover 详情**（估算值/上限/百分比 + hover 明细：剩余、输出上限、来源）。两者理念一致（低调 + 详情分层），ZCode 多了分段构成与缓存命中率。

### 1.6 Markdown 格式化按钮

**没有常驻的粗体/斜体/链接按钮**（bundle 中 testid 枚举未见，属"无证据"结论，中可信度）。编辑器是纯 Markdown + `@`/`/`/`$` 快捷输入；富文本栈侧重渲染（streamdown/marked/KaTeX/mermaid/shiki）而非所见即所得编辑。

→ **印证我们前一批 F3「移除 8 个常驻格式化图标」的方向与业界一致。**

---

## 2. MimoCode（小米 MiMo Code）

**来源**：https://mimo.xiaomi.com/coder 、https://mimo.mi.com/docs/zh-CN/tokenplan/integration/mimo-code 、https://mimocode.org/

- **已确认**：小米 MiMo 团队的**终端原生 AI 编程助手（CLI agent）**，安装 `curl -fsSL https://mimo.xiaomi.com/install | bash`；模式有 Build / Plan / Compose；CLI 有 `/init` `/connect` `/models` `/thinking` 等斜杠命令。另有 MiMo Desktop。
- **输入框布局**：**未找到公开资料**。CLI 形态本身无 GUI composer；Desktop 的输入框布局无文档/截图说明。
- **推断（非事实）**：若指 Desktop，按同类惯例可能是单个大圆角容器 + 底部工具条（左工具/附件、右模型/发送），无常驻 Markdown 按钮。

---

## 3. WorkBuddy（腾讯）

**来源**：https://www.workbuddy.cn/ 、https://www.workbuddy.cn/docs/workbuddy/Overview 、https://cloud.tencent.com/product/workbuddy

- **已确认**：腾讯全场景 AI 工作台/Agent，覆盖 IDE、插件、CLI、桌面端、小程序、移动端；对话区含任务标题栏、消息列表、输入框。
- **输入框布局**：**未找到公开资料**。
- **推断（非事实）**：作为 Agent 工作台，可能是大圆角卡片 + 工具条（左下工具/附件、右下模型/发送），无常驻 Markdown 按钮，用量/积分在账户区。

---

## 4. 与 AgentNodes 当前布局的逐项对照

| 维度 | ZCode（事实） | AgentNodes 当前（F4 后） | 差异 / 可借鉴点 |
|---|---|---|---|
| 容器 | 一个大圆角容器包住编辑器+工具条（`rounded-2xl`/16px） | 状态行在**框外**，输入框是另一个圆角容器（`rounded-xl`） | **结构性差异**：ZCode 工具条在框内。可考虑把状态行并入输入框容器，形成一个完整卡片 |
| 行结构 | 条件头部（编辑/附件）→ 编辑器 → 工具条 | 框外一行状态 → 图片预览 → slash 菜单 → 输入框（textarea + 上传 + 发送） | 缺少「编辑器内的头部插槽」（编辑态/附件 chips） |
| 左下工具 | `+` 更多、项目/工作区、模式、后台任务、worktree、审批、CUA | 无左侧工具区（ persona/模式在框外状态行） | ZCode 左侧信息密度高得多；但我们刚把工具收到一行，方向不同 |
| 模式选择 | 在**左下工具条**，6 档 | 在框外状态行，2 档（Build/Plan） | 位置不同；档位少，保持简单即可 |
| 模型选择 | 右下，provider+model+effort 一体 | 框外状态行，仅 model | 可借鉴「模型 + 推理档位」合并为一个 popover |
| 推理档位 | 独立滑杆（8 档） | 独立的思考模式按钮（Auto/On/Off） | 我们已有，粒度不同 |
| 用量 | 工具条图标 + 向上弹层，**7 段分段进度** | 行内紧凑文本 + hover 详情 | 我们刚做 F4-b 细化；可借鉴分段构成（但需后端支持 7 类来源拆分） |
| 发送按钮 | 圆形品牌蓝实心箭头 | 圆角矩形灰底 `ua-primary-action` + 发送图标 | **视觉差异明显**；可借鉴圆形 CTA |
| 语音输入 | 有（麦克风 + 波形） | 无 | 可选项，需评估需求 |
| 排队消息编辑 | 容器内部顶部编辑条 + 取消 | 无 | AgentNodes 缺此能力（属功能缺口，非纯布局） |
| 常驻 Markdown 按钮 | 无 | **已移除**（F3） | ✅ 方向一致 |
| 窄屏折叠 | 按优先级依次折叠为图标 | `flex-wrap` 自然换行 | ZCode 更精细；我们当前 375 宽 2 行可接受 |

## 5. 建议（供讨论，未实施）

按"借鉴价值 / 改动成本"排序：

| 优先级 | 建议 | 依据 | 成本 |
|---|---|---|---|
| **A（高价值）** | **把状态行并入输入框容器**，形成一个 `rounded-2xl` 大卡片：编辑器在上、工具条在下（persona/模式/模型/思考/用量 全部在框内底部） | ZCode 一手源码的结构性共识；当前框外状态行是最大视觉差异 | 中（Composer + StatusBar DOM 重组 + 基线重录） |
| **B（高价值）** | **发送按钮改圆形品牌色实心箭头** | ZCode/Cursor/Claude 都如此；当前灰底圆角矩形辨识度低 | 低（改 class） |
| **C（中）** | **用量弹层加分段构成**（messages/system_prompt/tool 等 7 类） | ZCode 的分段进度条信息量大 | 高（**依赖后端改造**：当前只有总量且无实测，见 context-usage-agent-survey §5） |
| **D（中）** | **编辑排队消息提示条**（含取消） | ZCode 一等能力；AgentNodes 有队列但无编辑态 UI | 高（功能+布局，需另案） |
| **E（低）** | 语音输入、worktree/CUA 入口 | ZCode 有，但属功能扩展 | 高，且非用户当前诉求 |
| **不做** | 恢复常驻 Markdown 按钮 | ZCode/MimoCode/WorkBuddy 均无 | — |

## 6. 需要用户决策的点

1. **是否做 A（框内工具条）**：这是最接近 ZCode 的结构性改造，会改变现有视觉与基线。
2. **是否做 B（圆形发送按钮）**：低风险、收益直观，可独立做。
3. **C 依赖后端**：当前用量连实测值都没有（详见 `context-usage-agent-survey-20261124.md`），分段构成需等后端透传后才能落地。
4. **D 是功能缺口非布局**：是否纳入后续队列能力规划。

## 7. 来源清单

- ZCode：`tmp/probe/zcode_ui/out/renderer/assets/styles-a4AckxCl.js`（bundle，行:列见正文）、`styles-D4aYlB9N.css`、`docs/ui-zcode-source-research-20261114.md`、用户截图（仅像素旁证）
- MimoCode：https://mimo.xiaomi.com/coder 、https://mimo.mi.com/docs/zh-CN/tokenplan/integration/mimo-code 、https://mimocode.org/
- WorkBuddy：https://www.workbuddy.cn/ 、https://www.workbuddy.cn/docs/workbuddy/Overview 、https://cloud.tencent.com/product/workbuddy
- 对照：`webui/frontend/src/components/chat/Composer.tsx`、`webui/frontend/src/components/chat/ComposerStatusBar.tsx`
