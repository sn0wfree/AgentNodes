# Grok Bot UI 设计语言源码级逆向 + AgentNodes 对照（调研）

> 2026-11-16 · 分支 `ui-workbench` · 状态：**调研稿（未改任何设计文档）**
> · 对象：Grok Bot Desktop **v1.1.0**（Cursor / Anysphere 出品，`sandBuiltAt=2026-11-13T19:12:57Z`）
> · 证据等级：**源码级**（自 `app.asar` 57MB 解包，非截图推断）+ **官方文档级**（8 页全文）
> · 关联：`docs/ui-zcode-source-research-20261114.md`（同口径第一份）·
> `docs/design/ui-workbench-design-20261115.md`（主文档）·
> `docs/design/ui-a-token-system-design-20261114.md`（U-A 方案）

## §0 结论摘要

1. **它和 AgentNodes 是同一类产品，但不是同一套价值观**。Grok Bot 做的是"**给 AI 一台电脑**"的
   托管 agent（云端 desktop + 浏览器 + 终端，跨任务持久化），UI 的价值主张是
   **"像给队友发消息"**（官方原话：*Grok Bot 的设计目标是让你感觉像在给队友发消息*）。
   AgentNodes 是**决策层/工作台**，Grok Bot 是**外包同事**。可借的是纪律，不是产品形态。
2. **令牌体系结构上和 U-A v2 高度同构，且比 ZCode 更激进**：一个"墨色基"
   `--cursor-base` 用 `color-mix` 派生出 **五级背景阶梯 + 四级描边 + 四级文本/图标**，
   双主题靠**两套 alpha 表**（不是两套色值表）切换。这套机制的严格程度超过 ZCode。
3. **配色纪律比 AgentNodes 现状干净得多**：主色只有 `--cursor-accent: #5ca3fe`（天蓝）
   + `--cursor-brand: #f54e00`（Cursor 橙，几乎不用）。其余是 10 个色族 × 8 档的角色色，
   **只出现在状态/角色语义位**。反观 AgentNodes 现状：渐变令牌×5 + 玻璃三类 + shadow-glow
   + aurora 四层 + amber×41 文件。
4. **`gradient` 232 处逐条分类后，装饰渐变只剩 1 处**：
   `mask-image` 里 **140 处**（渐隐遮罩/溢出淡出/`conic-gradient` 八向箭头）+
   `background-image` 里 **78 处**，后者再分：**19 处是同色双停靠垫片**
   （`linear-gradient(var(--x),var(--x))`，用途是给 background-color 做叠层叠加，
   不是渐变）+ **7 处径向点阵**（条纹/虚线纹理）+ **8 处 `color-mix` 令牌混合**
   + **2 处 shimmer 微光** + **41 处"其他"里绝大多数是 1px 分隔线/发丝线**
   （`transparent 44% → text-tertiary 46%..54% → transparent 56%` 这种斜切 1px 线）。
   **真正的多色装饰渐变只有 1 处**：`.ui-xio6eo{linear-gradient(135deg,#2563eb,#7c3aed,#db2777)}`，
   而且 **该 class 在全部 603 个 JS chunk 里零引用 —— 是死代码**。
   **结论必须写进 U-A v2 的 hygiene 门**：不能按 `gradient` 关键字一刀切，
   否则误伤 140 处合法 mask + 19 处垫片 + 发丝线。白名单应按
   **"是否多色 + 是否非 mask + 是否非单色垫片"** 三个条件判定。
5. **它有玻璃，而且是可切换的**：`body[data-cursor-glass-mode=true]` + `backdrop-filter`×21 +
   `glass.scss / glass.stylex` 独立 @layer + `--glass-chat-bubble-background`。
   **玻璃是可选皮肤，不是默认骨架**——这与 ZCode「渐变全锁在 highspeed-*/wf-* 命名空间」
   是同一条治理原则的两种实现。AgentNodes 要拆玻璃，拆的是**无条件玻璃**，不是玻璃本身。
6. **密度：13px 正文（不是 14）+ 15px transcript**。`--cursor-font-size-base:13px`、
   `transcript:15px`、对话区实际 `--conversation-font-size:14px / line-height:22px`。
   有 4 档高度令牌（20/24/28/32）与 5 档行高（14/16/18/22/24）。**没有用户可调字号旋钮**
   ——ZCode 有 `--ui-font-size`，Grok Bot 没有。这是个明确的三方分野。
7. **间距阶梯 0.25 步进**：`--cursor-spacing-0-25:1px` 到 `-20:80px`，共 **40 档**
   （1–12px 段按 0.25 细分，之后按 4px 一档）。**这正好回应 U-A v2 §10.6「任意值 px
   9/10/11 三档并存」的问题**——不需要发明刻度，直接采用 4px 基数 + 0.25 细分。
8. **主题注入是运行时契约**：`Uz` 字符串列出全部可注入 token 名
   （`sidebar.chrome.pane.elevated.base.accent...`），支持 `oklch()` 运行时转 hex。
   即**主题可被外部（企业/皮肤）整包替换**，组件零改动。这是 U-A「L1 语义层」的加强版。

## §1 取证口径（可复现）

| 步骤 | 动作 | 产物/规模 |
|---|---|---|
| 1 | `cursor.com/download/bot` 解析出下载清单 | 6 平台 × 12 包 |
| 2 | 下载 `win32-x64/1.1.0/Grok_Bot_1.1.0_Setup.exe` | 127,403,656 B |
| 3 | `7zr` 仅解 `resources/app.asar`（**不运行安装程序**） | 57,252,462 B |
| 4 | `@electron/asar extract` | 645 文件；`dist/{deps,electron-main,electron-preload,renderer,node-agent-coordinator}` |
| 5 | CSS 令牌普查 | `index-BkOHlEB9.css` 822,862 B / 580 声明在 `:root` |
| 6 | 主题表定位 | `index.eager-common-DcDxc78C.js` → 两套 alpha 表 + 调色板 |
| 7 | 主题注入契约 | `index.eager-platform-B-dP4sqR.js` → `Uz` token 名串 + oklch 转换器 |
| 8 | UI 词汇计数 | 全 603 个 chunk 字符串字面量扫描 |

**声明**：只做静态阅读，不运行、不登录、不联网调用其服务。所有数字可在上述文件复现。

## §2 技术栈（与 ZCode / AgentNodes 三方对照）

| 维度 | Grok Bot 1.1.0 | ZCode 3.18.2 | AgentNodes webui |
|---|---|---|---|
| 样式方案 | **StyleX 0.18.3**（编译期原子 CSS，`@layer anysphere.tokens/scss/stylex`） | Tailwind **v4**（oklch/@theme/lightningcss） | Tailwind v3 + slate-remap shim（v4 未挂载） |
| 组件基础 | **Base UI 1.9.0**（`@base-ui/react`）+ 自研 `xds-*` | radix 裸用 + 自研（`data-slot` 仅 7 个） | radix×4 + 自研 |
| 富文本 | **TipTap 3.14**（mention/suggestion/placeholder 全家桶） | — | plain textarea |
| 动效 | **motion 12.18** | CSS transition 为主 | framer-motion（拟删） |
| 状态 | TanStack Query + 自研 store | Context×79 + useSyncExternalStore×46 | zustand×16 |
| 拖拽 | **dnd-kit 3 件套** | — | — |
| 主题 | **JS 运行时注入 CSS 变量 + oklch 转换** | CSS 变量 + 双主题 | CSS 变量 + ThemeStore |
| 主进程 | Electron（`electron-main/main-app.cjs` 含 205 处 glass） | Electron 41 + Ink TUI 内核 | 浏览器 |
| 玻璃 | **`data-cursor-glass-mode` 可选皮肤** | 无（渐变锁命名空间） | 无条件玻璃×10 文件（拟拆） |

**判断**：StyleX 的收益是"令牌与组件同源、无类名拼写漂移"；代价是**没有工具类生态**，
组件必须写成 `styles={...}` 对象。AgentNodes 若坚持 Tailwind v4，不必换成 StyleX——
**要抄的是它的令牌分层纪律，不是它的样式方案**。

## §3 令牌体系全解（源码级）

### 3.0 三层令牌命名空间（结构发现）

CSS 里并存三套前缀，且各有独立的 class 家族，说明是**三层独立构建**：

| 前缀 | 归属 | CSS class 数 | 被引用 token 数 | 例 |
|---|---|---|---|---|
| `--cursor-*` | **平台层**（Cursor/VS Code 血统，`--vscode-*` fallback 可达） | `ui-*` 5,882 | **465** | `--cursor-bg-secondary`、`--cursor-font-size-base` |
| `--sand-*` | **应用层**（Grok Bot 自有） | `sand-*` 4,456 | **225** | `--sand-bg-accent-secondary`、`--sand-shimmer-ink` |
| `--ui-*` | **组件层**（单组件内部变量） | 同上共享 | — | `--ui-button-bg-hover`、`--ui-sidebar-row-fg` |

**这是比 ZCode「四层结构」更硬的证据**：不只是文档上的分层，而是**两套 StyleX class 家族 +
两套前缀**，可以从 token 名字反推归属。`--vscode-*` 变量的存在（`var(--vscode-editor-background,
var(--cursor-bg-editor))`）证明 `--cursor-*` 是**从 Cursor 主产品继承的平台层**，
Grok Bot 应用层在其上做 `--sand-*` 覆盖。

**对 AgentNodes 的启示**：U-A v2 的 L0/L1/L2 三层命名应该**可从前缀反推**。
现状 `canvas-*` / `wf-*` / `dag-*` 已经是这个方向，但缺少"平台层 vs 应用层"的区分。

### 3.1 唯一墨色基 + 双 alpha 表（最重要的机制）

`:root` 里只有一个真实色值承担全部中性层级：

```
--cursor-base: #f5f5f5          /* dark 主题下的"墨"= 白 */
--cursor-bg-primary:   color-mix(in srgb, var(--cursor-base) 20%, transparent)
--cursor-bg-secondary: color-mix(in srgb, var(--cursor-base) 14%, transparent)
--cursor-bg-tertiary:  color-mix(in srgb, var(--cursor-base) 8%,  transparent)
--cursor-bg-quaternary:color-mix(in srgb, var(--cursor-base) 6%,  transparent)
--cursor-bg-quinary:   color-mix(in srgb, var(--cursor-base) 4%,  transparent)
--cursor-stroke-primary:   color-mix(in srgb, var(--cursor-base) 20%, transparent)
--cursor-stroke-secondary: color-mix(in srgb, var(--cursor-base) 12%, transparent)
```

**但真正驱动主题的是 `index.eager-common` 里的两张 alpha 百分比表**，light/dark 各一张，
同 key 不同 alpha。**这才是"双主题免配色表"的完整实现**（ZCode 只做到 bg 三档，
Grok Bot 做到 **53 个语义位全表**）。

light 表（节选，完整见附录 A）：
```
bg/scrim 50       bg/scrim-heavy 89.7   bg/hover 6        bg/active 8
bg/secondary 6    bg/tertiary 6         bg/quaternary 4
fill/bubble-agent 6   fill/bubble-user 100   fill/attachment-ink 18
text/secondary 68 text/tertiary 56      text/quaternary 40
icon/primary 92   icon/secondary 66     icon/tertiary 52  icon/quaternary 32
border/primary-translucent 24  border/secondary-translucent 12
specular/primary 56  specular/secondary 16  specular/tertiary 12  specular/quaternary 6
shadow/* 12（唯一非零档：control/card/secondary 全在 light 有值、dark 全 0）
```

dark 表（同 key）：
```
bg/scrim 69.7   bg/secondary 4   bg/tertiary 4   bg/quaternary 2
fill/bubble-agent 4   fill/bubble-user 18   fill/attachment-ink 24
text/secondary 66  text/tertiary 54  text/quaternary 38
icon/primary 90  icon/secondary 64  icon/tertiary 50  icon/quaternary 30
shadow/* 全 0   specular/primary 12（light 是 56）
```

**四个可直接抄的洞察**：

1. **light 主题的 alpha 普遍比 dark 高**（text 68→66 变化小，但 `fill/bubble-user` 100→18、
   `specular/primary` 56→12）。**因为深色底上同样 alpha 的白色更"跳"**。
   AgentNodes 若照搬"同一 alpha 双主题通用"，dark 会过曝。
2. **`fill/bubble-user`: light=100%（纯墨实心）/ dark=18%（半透明）**。这是"用户气泡"
   在两种主题下**语义完全不同**：light 下是实心深色气泡（类似 ZCode 的反白 CTA），
   dark 下才是"墨 18%"的通透气泡。**U-A v2 §2 精髓#2 写的"用户气泡 = 墨 5%"只对 dark 成立**，
   需要按主题分档。
3. **shadow 在 dark 全为 0，light 才有值**——深色界面不用阴影分层，用 specular 高光。
   这条直接推翻 AgentNodes 现状的 `shadow-glow` 用法。
4. **`specular/*` 是独立一族**：`#ffffff1f / 08 / 05 / 03`（12/3/2/1%），用于顶边高光、
   玻璃边、squircle 描边。这是 AgentNodes 完全没有的词汇。

### 3.2 五表面 + 角色色矩阵（主题注入契约）

`index.eager-platform` 里的 `Uz` 字符串是**完整的可注入 token 名清单**：

```
表面族  sidebar . chrome . pane . elevated . base          （5 个）
强调    accent . accentFill . accentForegroundBright . onPrimary . brand . focus
状态四件套 ×3  role ∈ {success, warn, danger, added, modified, removed, untracked}
         每组 = role . roleFill . roleFillHover . roleFillPressed . roleFillBright
                     . roleForeground . roleForegroundHover . roleForegroundBright
角色色族 ×8  red . orange . yellow . green . cyan . blue . magenta . purple . brown
         同上四态 + 三前景 = 10 个 token/族
终端       terminalAnsi{Black..BrightWhite} ×16
```

**关键设计**：状态色不是"一个颜色"，而是**10 个 token 的完整语义组**
（fill / fillHover / fillPressed / fillBright / fg / fgHover / fgBright）。
AgentNodes 的 amber×41 问题正是**只有颜色没有组**——所以 144 处各写各的组合。
U-A v2 的 `StatusBadge` 组件化方向正确，但**组要定义成 10 token**，不是 3 个。

### 3.3 调色板（10 族 × 8 档 × 双主题，实测全表）

> 完整表见 **附录 B**。这里只列设计结论。

- **neutral 只有 7 档且语义非常规**：`neutral/0=#ffffff, /1=#ffffff, /2=#fcfcfc, /3=#f5f5f5,
  /4=#363636, /5=#1c1c1c, /6=#525252`（light）。**0 和 1 同值**——说明色阶表是给
  "组件语义槽位"用的，不是给"亮度梯度"用的。dark 侧 `0=#1f1f1f … 3=#0e0e0e`（越小编号越亮）。
- **每族 8 档的编号语义是"用途"不是"明度"**：`/0` 最亮（hover/聚焦）、`/1` 主填充、
  `/2` pressed、`/3` 边界、`/4-/6` 深背景、`/7` 特殊（暗色饱和）。
- **角色色的 dark 侧是"反相"的**：light `red/0=#fb2b3e`（亮红），dark `red/0=#fc2c3e`（更亮），
  但 dark `red/6=#ffd2cf`（浅粉）——**dark 侧的 /4-/6 是"浅色在高对比暗底上"**，
  用于 warning surface 之类的大面积填充。这跟 light 侧 /4-/6 是深色**方向相反**。
  **不能简单地把一张表的顺序反转**。

### 3.4 尺度令牌（完整，可直接采用）

**间距**（40 档，4px 基数 + 0.25 细分）：
```
0-25=1  0-5=2  0-75=3  1=4   1-25=5  1-5=6  1-75=7
2=8     2-25=9 2-5=10  2-75=11 3=12  3-25=13 3-5=14 3-75=15
4=16 ... 20=80（每 4px 一档，档间 0.25 细分）
```

**圆角**：`none 0 / xs 2 / sm 4 / base 6 / lg 8 / xl 12 / 2xl 14 / 3xl 16 / 4xl 18 / full 9999`

**字号**：`xs 11 / sm 12 / base 13 / lg 14 / transcript 15`
**行高**：`xs 14 / sm 16 / base 18 / lg 22 / transcript 24`
**字重**：`normal 400 / medium 500 / semibold 600 / bold 700`（code 变体独立）

**控件高度**：`sm 20 / md 24 / lg 28 / xl 32`
**动效**：`instant 50ms / fast .1s / normal .15s / slow .2s / slower .3s / drawer .5s / 1s / 2s`

**对话区专属**（这是最有借鉴价值的一组）：
```
--conversation-bubble-max-width: min(640px, calc(100% - 0px))
--conversation-opposed-inset:    clamp(0px, (100% - 480px) * .4, 20%)
--conversation-opposed-inset-human: max(32px, var(--conversation-opposed-inset))
--conversation-font-size: 14px
--conversation-line-height: 22px
--conversation-surface-border-radius: var(--cursor-radius-xl)   /* 12px */
--conversation-text-inset: 0px | var(--conversation-glass-text-inset)
--cursor-placeholder-opacity: .77
```

**读出三件事**：
1. **气泡最大宽度 640px 且随容器收缩**——生成式 UI 卡片也在这个宽度内。
2. **"对侧缩进"是流体函数**：容器宽于 480px 时才按 40% 缩进，上限 20%，人类消息最少 32px。
   这比 AgentNodes 的固定 padding 更优雅。
3. **placeholder 不透明度 0.77**（不是常见的 0.5）——保可读性。

### 3.5 密度：没有旋钮，但有明确刻度

Grok Bot **没有** `--ui-font-size` 这类用户可调密度。它选择**固定 13px 正文 / 14px 对话 /
15px transcript**，靠**控件高度四档**（20/24/28/32）表达密度差异。

对照：
- ZCode：`--ui-font-size: 14px` + calc 派生（**可调**）
- Grok Bot：固定刻度（**不可调**）
- AgentNodes：9/10/11px 任意值三档并存（**无刻度**）

**建议**：U-A v2 §6.4（aurora 替代）之外应新增一条——**先立刻度、再谈可调**。
没有刻度时给旋钮是伪需求；AgentNodes 现状是"三档任意值"，第一步是收敛到
11/12/13/14/15 + 四档行高，第二步才是可调。

### 3.6 动效与可达性

- `prefers-reduced-motion` **12 处**（ZCode 20 处，量级相当）。
- 动效时长上限 `.5s`（drawer），交互反馈 `.1–.2s`，numeral roll / tray 有专用 keyframes。
- `-electron-corner-smoothing: 60%` 在 glass 模式下对一批元素生效（Electron squircle）。
- **无 `prefers-color-scheme`**：主题完全由应用设置驱动（Follow System / Light / Dark 三选一），
  运行时注入。这避免了 ZCode 那种"跟随系统破坏手动切换"的坑（U-A v2 §9.2 已记）。

## §4 组件与交互词汇（实测）

### 4.1 组件词汇（chunk 名 + `xds-*` 前缀）

`xds-` = 自研设计系统前缀：`xds-button`(43)、`xds-modal-surface`(12)、`xds-modal-footer`(12)、
`xds-callout-body/action`(3)、`xds-input`(3)。

另有明确命名的基础件：`SegmentedControl`、`ToggleIconButton`、`CompositeRoot`、
`ComboboxItemIndicator`、`CodeBlock`、`CodeTokenizerProvider`、`pane-header`、
`settings-row`、`list-item`、`list-card`、`scroll-pane`、`pagination-dots`、`member-chip`、
`stack-pager`、`scale-layer`、`indicator`。

**产品级组件（chunk 家族，603 个 chunk 里的语义名）**：

| 域 | 组件 |
|---|---|
| Bot 管理 | `bot-configure-dialog`、`bot-manage-screen`(106KB)、`bot-manage-knowledge`、`bot-manage-ready`、`bot-manage-warm-up`、`bot-manage-section`、`bot-manage-automation-drilldown` |
| 分享 | `bot-share-popover`(72KB)、`bot-share-invite-popover`、`bot-share-control`、`bot-share-state-gate`、`bot-share-availability`、`bot-share-interruption`、`bot-sharing-reads` |
| 审批 | `auto-review-selector`、`auto-review-rule-notice`、`sand-auto-review-instructions`、`sand-rule-text-diff` |
| 例行任务 | `automation-detail-panel`、`automation-run-rows`(38KB)、`bot-manage-automation-meta` |
| 电脑 | `computer-screen-preview`、`desktop-mount-panel`、`capture-overlay`、`spotlight-element-pick-overlay` |
| 聊天件 | `prompt-editor`(90KB)、`new-chat-bar`、`inline-user-form`、`voice-memo-bubble`、`voice-call-feedback-card`、`draft-send-error`、`use-draft-revision-highlight` |
| 文件/产物 | `fullscreen-file-viewer-body`、`library-visual-viewer`、`bot-store-files-library`、`document-page`、`markdown-math-katex` |
| 连接器 | `connector-card`、`connector-card-view`、`native-connector-card`、`custom-mcp-form`、`plugin-detail-view` |
| Vault | `vault-card-dialog`(146KB)、`vault-card-editor`、`grok-bot-vault-login-dialogs`(143KB) |
| 生成式 UI | `visualization-d3-runtime.gen`(284KB)、`visualization-three-runtime.gen`(696KB)、`visualization-document` |
| 移动端 | `phone-call`、`phone-app-qr-sheet`、`mac-app-tile`、`mac-mail-setup-dialog` |

**注意 `*.gen.js` 后缀**：可视化运行时是**独立生成的 bundle**（d3 + three），
和主应用代码分离——这是 Gen-UI 层"重依赖隔离"的做法，U-E 可直接参考。

### 4.2 交互词汇（源码字符串实测，全部命中）

| 类别 | 词条（源码原文） | 出现文件数 |
|---|---|---|
| 审批四件套 | `Allow once` / `Always allow` / `Deny` / `Not now` | 1 / 2 / 4 / 2 |
| 审批变体 | `Skip` / `Manage settings` | 10 / 1 |
| 草稿 | `Needs approval` / `Send` / `Discard` | 2 / 16 / — |
| 侧栏三态 | `Needs attention` / `Unread activity` / working(often `Working`) | 2 / 1 / 5 |
| 过期 | `Expired` | 5 |
| 策略 | `Auto-review`(17 文件) / `Ask first` / `Custom Rules` | 17 / 2 / 1 |
| 电脑接管 | `Open computer` / `Take control` / `Take over` / `I'm done` | 1 / 1 / 2 / 1 |

**与 ZCode 的关键差异**：

| 能力 | ZCode | Grok Bot | AgentNodes 计划 |
|---|---|---|---|
| 审批粒度 | 四档（allowOnce/allowAlways/rejectOnce/rejectAlways） | **三档+策略**（Allow once / Always allow / Deny）+ Ask-first 规则 | U-B 四档（灰态） |
| 审批超时 | 未明确 | **聊天内无限等待 / 后台 30 分钟过期 → `Expired`** | 未设计 |
| 策略引擎 | `setPermissionMode` | **Auto-review（模型判定）+ Custom Rules（自然语言，各 20 条上限）** | 未设计 |
| steer | `turn.steerQueued/steerDrained`（协议级） | **自然语言 "Stop now"（不撤销已完成操作）** | U-D 灰态 |
| 回退/分叉 | fork 全套 + checkpointRestored | **无**（Bot 是持续工作，非会话回放） | U-D |

**这条差异是本轮最重要的产品判断**：Grok Bot **没有 rewind/fork/checkpoint 词汇**，
因为它把"工作"放在云端电脑上持续进行，而不是"会话回放"。
**AgentNodes 的 U-D（会话日志投影）不能拿 Grok Bot 当模板**——它的模板仍应是 ZCode。

### 4.3 玻璃层的实现（可切换皮肤）

```
@layer anysphere.tokens, anysphere.scss, anysphere.stylex, glass.scss, glass.stylex, overrides
body[data-cursor-glass-mode=true]  ← 运行时开关（17 处 CSS 引用）
--glass-chat-bubble-background
backdrop-filter: 21 处
```

**三层治理**：令牌层 → 组件层 → **glass 独立 layer（可整层关闭）** → overrides 兜底。
样式层级顺序本身表达了优先级：**玻璃永远不能覆盖 overrides**。

**blur 也是有刻度的**（21 处 `backdrop-filter` 实测分布）：
`4 / 6 / 8 / 10 / 12 / 16 / 20px` 七档，且多数写成 **`blur(var(--cursor-spacing-N))`**——
即**复用间距令牌当模糊半径**（`spacing-1=4px`、`1-5=6px`、`2=8px`、`2-5=10px`、
`3=12px`、`4=16px`、`5=20px`），把 blur 纳入同一套刻度。另有
`saturate(180%) blur(20px)` / `blur(16px) saturate(1.2)` / `blur(6px) saturate(.8)` 三个组合档，
以及 1 处 `backdrop-filter:none`（`[data-variant=media]` 上显式关掉）。

**对 AgentNodes 的修正建议**：主文档 §2 精髓#4 写"渐变/玻璃/辉光/aurora 拆除"，
应改为"**默认骨架禁玻璃；玻璃只能作为独立可关闭层存在，且必须走 `data-*` 开关 + 刻度化 blur**"。
理由：Grok Bot 证明玻璃本身不是罪，**无条件玻璃**才是；而且它的 blur 不是随手写的
magic number，是**复用间距令牌**——这才是可维护的写法。

## §5 界面骨架（文档级证据）

> 以下来自官方文档 8 页全文（`grok.docs-zh.com/zh-CN/grok-bot/*`），非源码。
> 文档未提供真实截图（页面内是文字模拟块），故**视觉细节以 §3 令牌为准，布局以本节为准**。

### 5.1 三栏 + 三 tab

```
┌──────────────┬────────────────────────────────┬──────────────────┐
│ Bot 列表      │  对话（消息流 + composer）        │  电脑预览          │
│ (侧栏)        │  工具活动/电脑操作/文件/问题/      │  (computer panel)│
│ + New Bot    │  审批请求/语音备忘 全部内联        │  [详情|资料库|电脑] │
│ Connect apps │                                │                  │
└──────────────┴────────────────────────────────┴──────────────────┘
```

- **Bot 详情三 tab**：`详情`（Details，含例行任务列表/时区）/ `资料库`（Library，文件）/
  `电脑`（Computer，实时屏幕 + 终端 + 浏览器）。
- **侧栏**：Bot 列表 + `+ New Bot`（Cmd/Ctrl+N）+ `Connect apps` + 搜索（Cmd/Ctrl+K）。
  支持 `Pin` / `Move to new section` / `Join` / `Hide from sidebar`，隐藏项归入 `Hidden Bots`。
- **侧栏三态**：`Needs attention`（待回答问题/审批/交接）/ `Unread activity`（新结果）/
  working 或 typing。
- **消息流内联一切**：普通消息 + 工具活动 + 电脑操作 + 创建的文件 + 问题 + 审批请求 +
  语音备忘 + 草稿卡。**没有独立的"运行面板"**——所有过程都在流里。

### 5.2 与 AgentNodes 主文档 §4 三表面蓝图对照

| 面 | Grok Bot | AgentNodes 计划 | 判断 |
|---|---|---|---|
| 会话 | 侧栏 Bot 列表 / 消息流+composer / 电脑预览 | 会话列表 / 消息流+composer / 工作台（审批+子代理+todo） | **右栏职能不同**：Grok Bot 是"看机器"，AgentNodes 是"看决策"。不可照搬 |
| 详情 | 三 tab（详情/资料库/电脑） | — | **可借鉴 tab 划分**：资料库=产物，电脑=执行环境 |
| 审批 | 内联卡片 + Auto-review 策略层 | 右栏工作台 + 四档 | Grok Bot 的内联卡 + 超时过期值得抄 |
| Trace | **无此面** | Trace 三栏 | Grok Bot 无 trace 视图，AgentNodes 是更强的 |
| RSI | **无此面** | RSI 三栏 | 同上 |

**结论**：Grok Bot 的**信息架构比 AgentNodes 窄**（它不做观测/进化面），但**会话面的
打磨深度更高**（内联一切 + 侧栏三态 + 审批过期语义）。AgentNodes 应抄会话面的细节，
不抄它的信息架构宽度。

## §6 借鉴清单（→ U-A v2 / U-B 修订建议，供决策，未改文档）

| # | 借鉴项 | 证据 | 落法 |
|---|---|---|---|
| 1 | **双 alpha 表**代替双色值表 | `C_`/`w_` 两张 **53 key** 全同键表 | U-A v2 的 L0 加"alpha 表"一层：同一语义位在 light/dark 有不同 alpha，不是不同 hex |
| 2 | **五级背景 + 四级描边 + 四级文本** | `bg/primary..quinary`、`stroke/primary..quaternary`、`text/secondary..quaternary` | 文本三级（ZCode）→ **四级**（Grok Bot）。新增 quaternary 给"禁用/占位" |
| 3 | **specular 高光族** | `#ffffff1f/08/05/03` | 新增 `--spec-1..4`，替代 shadow-glow 做"抬起"感 |
| 4 | **dark 不用阴影、light 不用高光** | shadow 表 dark 全 0 / light 12-56 | hygiene 门加一条：`dark` 下出现 `box-shadow` 装饰即告警 |
| 5 | **状态色 = 10 token 组**不是单色 | `roleFill/FillHover/FillPressed/FillBright/Foreground/ForegroundHover/ForegroundBright` | `StatusBadge` 按 10 token 定义，替代 amber×144 处手写 |
| 6 | **间距 4px 基数 + 0.25 细分（40 档）** | `--cursor-spacing-0-25..20` | U-A v2 补刻度表；取代 9/10/11 任意值 |
| 7 | **圆角 10 档** | `none 0 … full 9999` | 采用；`--radius-base:6px` 作为默认控件圆角 |
| 8 | **对话区流体缩进** | `clamp(0px,(100%-480px)*.4,20%)` + `max(32px,…)` | 直接采用，替代固定 padding |
| 9 | **`gradient` 需三条件分类** | 232 处：140 mask + 19 同色垫片 + 7 点阵 + 8 color-mix + 2 shimmer + 41 发丝线 + **1 处真装饰渐变（且零引用）** | **hygiene 门按"多色 & 非 mask & 非单色垫片"判定**，不能按关键字一刀切 |
| 10 | **玻璃作为独立可关层 + 刻度化 blur** | `glass.scss/glass.stylex` 独立 @layer + `data-cursor-glass-mode`；blur 复用间距令牌（4/6/8/10/12/16/20） | 修正主文档精髓#4：拆"无条件玻璃"，保留"可关玻璃层"；blur 走刻度 |
| 11 | **主题运行时注入契约** | `Uz` token 名串 + oklch→hex 转换 | U-A v2 L1 语义层做成"可整包注入"，为未来皮肤/企业主题留口 |
| 12 | **审批卡过期语义** | 聊天内无限 / 后台 30min → `Expired` | U-B 补"过期"状态（当前四档无此态） |
| 13 | **Gen-UI 重依赖隔离** | `*.gen.js` 独立 bundle（d3 284KB / three 696KB） | U-E 采纳：可视化运行时单独 chunk，不进主包 |

## §7 简化清单（明确不抄）

| 项 | 原因 |
|---|---|
| StyleX | 无工具类生态，组件写作成本高；AgentNodes 已选 Tailwind v4，迁移收益 > 换方案收益 |
| Base UI | radix 已够用；换库是大范围重构，无对等收益 |
| TipTap 全家桶 | AgentNodes composer 当前是 textarea；富文本/mention 是后续需求，届时再评估 |
| dnd-kit | 侧栏排序/分屏拖拽对 AgentNodes 非核心 |
| 云端电脑 / 浏览器 / 终端面板 | **产品形态不同**：AgentNodes 是本地决策层，不托管执行环境 |
| 群聊 / Bot 间交接 / Slack 集成 | 多 agent 协作形态，与 AgentNodes 单会话模型冲突 |
| 语音（dictation / voice chat / 语音备忘） | 非本轮范围 |
| Vault / 支付 / 1Password 集成 | 安全域自研，不做 |
| 10 色族全量 | AgentNodes 只需：轨迹五色 + 状态四件套 + viz 8 色。10 族是 Grok Bot 的"角色色"需求（Bot 有颜色标识） |
| 无密度旋钮 | ZCode 的 `--ui-font-size` 更贴合 AgentNodes"对话阅读型"定位 |

## §8 对现有文档的修订建议（不改文档，供确认）

### 8.1 对 `ui-workbench-design-20261115.md`

| 位置 | 现文 | 建议改为 | 理由 |
|---|---|---|---|
| §2 精髓#2 | "用户气泡 = 墨 5%" | "用户气泡 light=墨 100%（实心）/ dark=墨 18%（通透）" | Grok Bot alpha 表实测；5% 在 light 下不可见 |
| §2 精髓#4 | "渐变/玻璃/辉光/aurora 拆除" | "默认骨架禁多色渐变/辉光/aurora；**玻璃降为可关层**；hygiene 门按 mask/垫片/发丝线白名单判定" | Grok Bot glass layer 实证；232 处 gradient 里真装饰渐变仅 1 处且零引用 |
| §3 目标令牌 | 文本三级 | **文本四级**（+quaternary） | Grok Bot + ZCode 都隐含第四级 |
| §3 目标令牌 | 无 specular 族 | **新增 `--spec-1..4`** | 替代 shadow-glow 的唯一合理解 |
| §5 工序 A 批 | 未提刻度 | A 批加"**间距/圆角/字号刻度落地**" | 刻度是 B 批改写的先决条件 |
| §6.2 | amber 处置 | 补充"**状态色定义成 10 token 组**" | 否则 StatusBadge 只收敛表面 |

### 8.2 对 `ui-a-token-system-design-20261114.md`（v2 回写时）

新增两个修订点（接续 §10.7 的 9 个 → **11 个**）：
- ⑩ **双 alpha 表机制**：L0 层除色值外增加"alpha 语义表"，light/dark 各一套。
- ⑪ **specular 高光族**：`--spec-1..4` 作为"抬起"语义，与 shadow 分工
  （dark 用 spec，light 用 shadow）。

### 8.3 对 U-B（审批）

1. 补 **`Expired` 状态**：后台/例行任务发起的审批 30 分钟过期，聊天内发起的不限期。
2. 补 **策略层**：Grok Bot 的 `Auto-review`（模型判定）+ `Custom Rules`（自然语言规则）
   是"四档粒度"之外的第二层。U-B 只需记录这个概念，实施可延后。

## §9 信息局限（必须声明）

1. **无真实截图**：官方文档页内是文字模拟块，下载页无产品图，`x.ai/bot` 在本机网络不可达
   （curl 000）。两张 `showcase/*.webp` 实为壁纸渲染图。**故所有视觉判断来自 CSS/JS 源码，
   不是像素观察**。
2. **主题值以 dark 为主**：`:root` 是 dark 默认（`--cursor-chrome:#141414`），light 表在 JS 里。
   已取到两套 alpha 与调色板，但**组件级用色组合**（哪个组件用哪一级 bg）未逐组件核对。
3. **未运行**：不启动应用、不登录，故无法验证交互实测（如键盘导航、焦点环实际表现）。
4. **中文渲染未验**：与 AgentNodes §10.5.4 同样的问题，本轮未获得可观测样本。
5. **文档口径 vs 源码口径**：§4/§5 的 UI 词汇部分来自官方文档（可能描述的是更新版本），
   源码字符串计数是 v1.1.0 实测；两者一致的已标注，**不一致处以源码为准**。
6. **`--cursor-*` vs `--ui-*` 双命名空间**：`--cursor-*` 是 Cursor 平台层（可能来自
   cursor 主产品共享），`--ui-*` 是组件层。**哪些是本产品自有、哪些是继承**未完全分离。

## 附录 A · 双 alpha 表全量

```js
// light  (C_)
{"bg/scrim":50,"bg/scrim-heavy":89.7,"bg/hover":6,"bg/active":8,
 "bg/primary-disabled":15,"bg/secondary":6,"bg/secondary-hover":10,
 "bg/secondary-active":18,"bg/secondary-disabled":4,"bg/tertiary":6,
 "bg/quaternary":4,"fill/bubble-agent":6,"fill/bubble-user":100,
 "fill/attachment-card":0,"fill/attachment-paper":0,"fill/attachment-ink":18,
 "text/secondary":68,"text/tertiary":56,"text/quaternary":40,
 "icon/primary":92,"icon/secondary":66,"icon/tertiary":52,"icon/quaternary":32,
 "border/primary-translucent":24,"border/secondary-translucent":12,
 "border/tertiary-translucent":8,"border/quaternary-translucent":6,
 "border/attachment-card":8,"border/attachment-paper":12,
 "shadow/control":12,"shadow/card":3.5,"shadow/secondary":7,
 "shadow/inline/ambient":8,"shadow/inline/key":6,
 "shadow/popover/ambient":10,"shadow/popover/key":10,
 "shadow/modal/ambient":10,"shadow/modal/key":10,
 "shadow/window/ambient":56,"shadow/window/edge":10,"shadow/ring":.4,
 "specular/primary":56,"specular/secondary":16,
 "specular/tertiary":12,"specular/quaternary":6,
 "bg/{role}-primary-disabled":17,"bg/{role}-secondary":9,
 "bg/{role}-secondary-hover":17,"bg/{role}-secondary-disabled":6.1,
 "text/{role}-quaternary":13,"text/accent-quaternary":17,
 "text/danger-quaternary":17,"border/{role}-quaternary":17}

// dark  (w_)
{"bg/scrim":69.7,"bg/scrim-heavy":95,"bg/hover":6,"bg/active":8,
 "bg/primary-disabled":13,"bg/secondary":4,"bg/secondary-hover":8,
 "bg/secondary-active":20,"bg/secondary-disabled":2,"bg/tertiary":4,
 "bg/quaternary":2,"fill/bubble-agent":4,"fill/bubble-user":18,
 "fill/attachment-card":3,"fill/attachment-paper":10,"fill/attachment-ink":24,
 "text/secondary":66,"text/tertiary":54,"text/quaternary":38,
 "icon/primary":90,"icon/secondary":64,"icon/tertiary":50,"icon/quaternary":30,
 "border/primary-translucent":22,"border/secondary-translucent":10,
 "border/tertiary-translucent":6,"border/quaternary-translucent":4,
 "border/attachment-card":12,"border/attachment-paper":16,
 "shadow/control":0,"shadow/card":0,"shadow/secondary":0,
 "shadow/inline/ambient":0,"shadow/inline/key":0,
 "shadow/popover/ambient":0,"shadow/popover/key":0,
 "shadow/modal/ambient":0,"shadow/modal/key":0,
 "shadow/window/ambient":0,"shadow/window/edge":0,"shadow/ring":0,
 "specular/primary":12,"specular/secondary":3,
 "specular/tertiary":2,"specular/quaternary":1,
 "bg/{role}-primary-disabled":15,"bg/{role}-secondary":7,
 "bg/{role}-secondary-hover":15,"bg/{role}-secondary-disabled":4.1,
 "text/{role}-quaternary":11,"text/accent-quaternary":15,
 "text/danger-quaternary":15,"border/{role}-quaternary":15}
```

## 附录 B · 调色板 10 族 × 8 档（light / dark）

| family | /0 | /1 | /2 | /3 | /4 | /5 | /6 | /7 |
|---|---|---|---|---|---|---|---|---|
| neutral | #ffffff / #1f1f1f | #ffffff / #161616 | #fcfcfc / #121212 | #f5f5f5 / #0e0e0e | #363636 / #dbdbdb | #1c1c1c / #f5f5f5 | #525252 / #c1c1c1 | — |
| red | #fb2b3e / #fc2c3e | #e72e3b / #cf2b35 | #c82532 / #f73643 | #cd2834 / #ff6463 | #ad1f2a / #ff908b | #8e1921 / #ffb3ae | #6f1017 / #ffd2cf | #ab1e29 / #a8222a |
| orange | #fd690f / #f9670e | #ee620d / #cd5309 | #d2550a / #f4650e | #c14c00 / #fe6600 | #a23f00 / #ff9464 | #843200 / #ffb696 | #682600 / #ffd3c1 | #b64907 / #a84306 |
| yellow | #f59312 / #f69412 | #da830e / #c8770c | #c2730b / #ea8c10 | #aa6300 / #e28600 | #905300 / #fb9500 | #774400 / #ffb670 | #5f3500 / #ffd6b0 | #aa6409 / #a76308 |
| green | #14c16f / #14c370 | #10ab61 / #0e9f5a | #0d9655 / #13bb6b | #00854a / #00ba69 | #00723e / #29cb78 | #005f33 / #7fdca1 | #004b27 / #b8eac7 | #098249 / #0a844a |
| cyan | #13b7a2 / #13b8a3 | #10a693 / #0e9886 | #0d9381 / #12b39f | #008273 / #00b7a2 | #007062 / #59c8b5 | #005d51 / #8dd8c9 | #004c42 / #bce8de | #0a7f70 / #097d6e |
| blue | #1e86fc / #1884fc | #1680f3 / #116dd2 | #126fd4 / #1784fb | #0b6dd3 / #4d9bff | #085ab2 / #7cb5ff | #054991 / #a3caff | #033872 / #c8dfff | #0d5eb6 / #0c58aa |
| purple | #8f59f9 / #905afb | #8351e4 / #7845d7 | #7442d1 / #8e59f8 | #7547d0 / #a582ff | #623aae / #bba5ff | #4f2e8e / #cdbfff | #3d2270 / #e0d8ff | #6539b7 / #6036af |
| magenta | #f93498 / #fb3499 | #e52f8b / #cf297e | #c82779 / #f63397 | #c62378 / #ff55a4 | #a71c63 / #ff8ab9 | #881551 / #ffafcd | #6c0e3e / #ffcfe0 | #ab2067 / #aa2066 |
| brown | #a06b39 / #a16b39 | #9d6937 / #8c5d31 | #86592e / #aa723d | #996b42 / #c59e7d | #835a34 / #ccb39d | #6f4b2b / #dac7b7 | #5a3c21 / #e8ddd3 | #714a25 / #704925 |

## 附录 C · 关键单值令牌

```
--cursor-accent           #5ca3fe      （天蓝，唯一强调）
--cursor-brand            #f54e00      （Cursor 橙，几乎不用）
--cursor-base             #f5f5f5      （墨色基）
--cursor-chrome           #141414      // 窗口铬
--cursor-pane             #191919      // 面板
--cursor-sidebar          #101010      // 侧栏
--cursor-focus            #1f87fc      // 焦点环
--cursor-syntax-background #181818
--cursor-syntax-foreground #d6d6dd
--cursor-specular-primary   #ffffff1f
--cursor-specular-secondary #ffffff08
--cursor-specular-tertiary  #ffffff05
--cursor-specular-quaternary #ffffff03
--cursor-shadow-primary    #0003
--cursor-shadow-workbench  0 0 8px 2px color-mix(in srgb, var(--cursor-shadow-primary) 40%, transparent)
--cursor-stroke-media      #ffffff1f
--cursor-stroke-control-divider #00000029
--cursor-bg-media          #000000b8
--cursor-bg-media-hover    #0000008a
--cursor-on-primary        #f5f5f5
--cursor-text-secondary    #fcfcfc99
--cursor-placeholder-opacity .77
```

## 附录 D · 取证文件位置（临时，未入档）

```
tmp/grokbot/Grok_Bot_Setup.exe                      127 MB（签名安装包，未运行）
tmp/grokbot/extract/app.asar                         57 MB
tmp/grokbot/asar_out/                              645 文件（解包产物）
tmp/probe/gb_*.html, gb_*.txt                      官方文档 8 页原文
tmp/probe/{css_vars,tokens2,layout_tokens,scale_tokens,palette_table,alpha_maps,msgids,ui_copy,chunk_families}.{py,txt}
tmp/probe/7zr.exe                                  便携 7-Zip（仅用于解包）
```

> tmp/ 在 `.gitignore:124`，随用随弃。若需入档，按主文档 §9.4 建议移到
> `docs/mockups/` 只保留**结论表**，不保留二进制。
