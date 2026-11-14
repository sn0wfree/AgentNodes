# ZCode UI 设计语言源码级逆向 + AgentNodes 逐维度对比（调研，R-014 前置）

> 2026-11-14 · 对象：ZCode Desktop **v3.18.2**（本机安装，`D:\Software\ZCode`，Electron）
> · 方法：app.asar 自校验解包（367MB / 31,961 文件 / 应用代码 4,251）→ 渲染层
> CSS 令牌普查（styles-*.css 492KB）+ 入口 bundle 签名扫描（7.0MB）+ CLI 内核指纹
> （zcode.cjs 14.9MB）+ AgentNodes webui 同口径镜像统计（tmp/probe/*，全部实测）

## §0 结论摘要

**一句话哲学：ZCode 的 UI 是一块中性仪表板——骨架（灰阶）承重、颜色只承载
意义（状态/类型/环境）不承载情绪、情绪被隔离进特性令牌命名空间、一切层级用
「一个墨色 + 透明度」表达。**

- 三条最值得借鉴：① `primary=黑 + brand=天蓝` 的双轨色（CTA 归中性、强调色只做
  语义标记）——比 U-A 现方案的「单强调蓝」更完整；② 文本三级层级
  （foreground/subtle/subtlest）+ 「墨色透明度」表面系统（选中/hover/气泡 =
  黑色 3-8% alpha）——灰阶语义化的一次性收敛；③ 特性命名空间治理
  （highspeed-*/wf-*/remote-* 各自成 token 集，情绪永不出圈）。
- 三条明确不抄：① Context×79 + useSyncExternalStore×46 的状态方案（比 zustand
  大面积极大）；② 3MB 的 react-intl 全量 i18n；③ 640KB Gen-UI 沙箱运行时
  （等 U-E 再说，且只需最小实现）。
- 对 U-A 的直接影响：**Tailwind v4 `@theme` 迁移可以顺带杀死 slate-remap shim**
  （ZCode 的 `text-foreground-subtle` 工具类就是 v4 @theme 直出）；U-A 的
  「单强调蓝」建议升级为「黑 primary + 蓝 brand」双轨。

## §1 ZCode 架构与框架选择

### 1.1 进程拓扑（out/ 实测）

```
app.asar
├── out/main/            Electron 主进程（index.js 898KB）— node-pty/sharp/ssh2/keyring
├── out/host/            本地 Agent 宿主服务（index.js 2.4MB + chunks）
├── out/extension-host/  扩展宿主（1.4MB）
├── out/scheduler/       调度器（1.2MB）
├── out/plugin-sandbox/  Gen-UI 沙箱（gen-ui.js 640KB + vendor lucide 1.17 + d3 7.9）
├── out/preload/         6 个 preload 面：codingPlanWebview / rewardsWebview /
│                        cuaPermissionPanel / resourceManager / pluginSandbox /
│                        embeddedBrowserJavaScriptDialog
└── out/renderer/        React 渲染层（入口 + 7.0MB 主 bundle + 492KB CSS）
     └── workers: diffs.worker 827KB / pdf.worker 1.0MB
```

- 渲染层 ↔ host 经 **JSON-RPC over WebSocket**（rpc×105、jsonrpc×15 命中）——
  渲染层不是权威，只是投影（与调研 §8.1「视图即投影」同构）。
- diff 计算独立 **worker 线程**（827KB）；CUA 审批走**独立 preload 面**
  （cuaPermissionPanel）——高信任交互物理隔离。

### 1.2 框架栈（package.json + bundle 签名实测）

| 层 | 选择 | 证据 |
|---|---|---|
| 框架 | React 19.2.4 | package.json + react-*.js chunk |
| 构建 | Vite（hash 命名 + modulepreload 清单） | index.html |
| CSS | **Tailwind v4**（oklch 全量调色板 / `*,:before,:after,::backdrop` 93 变量 preflight / `outline-hidden` / lightningcss 指纹 / --tw-×129） | CSS 普查 |
| 组件 | radix 系 + shadcn 风格 `data-slot`（select/command/dialog/popover/scroll-area/input-group/alert/avatar/context-menu/combobox）+ cmdk + vaul | bundle 签名 |
| 状态 | **Context×79 + useSyncExternalStore×46 + immer×19；zustand 0；redux 0** | bundle 签名 |
| 路由 | **无 react-router**（createBrowserRouter/useNavigate 均 0）——tab/workspace 式导航 | bundle 签名 |
| 虚拟化 | virtua + virtualizer（非 react-virtuoso） | bundle 签名 |
| 终端 | **xterm in renderer** + node-pty in main | 签名 + 原生模块 |
| 富文本 | streamdown（流式 markdown）+ marked/remark/unified/hast + **KaTeX**（自带字体）+ **mermaid** + shiki | 签名 + @font-face |
| 图 | cytoscape（workflow 图）+ recharts（CSS 选择器证实） | 签名 |
| 动画 | framer-motion + 54 个手写 keyframes | 签名 + CSS |
| 图标 | lucide（createLucideIcon chunk） | index.html |
| i18n | react-intl 系（IntlProvider chunk 2.3MB 消息包） | chunk |
| 监控 | OpenTelemetry 全家桶 + @arms/rum-electron | package.json |
| 字体 | **系统栈**：`ui-sans-serif, system-ui...` / mono 同理——**零品牌字体自带**（唯一 @font-face 是 KaTeX） | CSS 普查 |

### 1.3 CLI 内核（对照「TUI 系」）

`glm/zcode.cjs`（14.9MB）：**ink×1562**、structuredPatch×29、ripgrep×24——
Ink TUI 路线（同 Claude Code），desktop 与 TUI 共享内核、各自投影。

## §2 设计令牌体系全解（569 去重变量实测）

### 2.1 四层结构

```
L0 原语调色板   oklch 全量（neutral/blue/sky/green/red/yellow/amber/violet/
                teal/cyan/emerald/...）——v4 @theme 直出，只有用到的档位
L1 语义角色     shadcn 基座（background/foreground/card/popover/primary/
                secondary/muted/border/input/ring）+ ZCode 扩展
L2 功能词汇     diff-*、git-*、node-*、remote-*、terminal-*、trajectory-*、
                context-breakdown-*、usage-chart-*、interaction-*
L3 特性命名空间 highspeed-*（48 个）/ wf-*（13 个）/ zcode-*（4 个）/
                composer-thought-*——情绪与特性色封死在命名空间内
```

### 2.2 关键语义决策（每条都有实测值）

| 决策 | 实测 | 含义 |
|---|---|---|
| **primary = 中性黑** | `--color-primary: var(--color-neutral-950)`；light 下 foreground = neutral-700（非 900） | CTA 是黑色按钮，**不占情绪** |
| **brand = 单一天蓝** | `--color-brand: var(--color-sky-400)`；accent = sky-50（表面淡蓝） | 强调色只做语义标记（图标/链接/focus） |
| **文本三级层级** | foreground / foreground-subtle / foreground-subtlest（+inverse） | 灰阶信息层级显式成 token，不许就地调灰 |
| **墨色透明度表面** | `--color-surface: #0d0d0d08`（3% 黑）/ `--color-selected: #0d0d0d0d`（5%）/ `--color-user-message-bubble: #0d0d0d0d` / hover 同族 | **用户气泡 = 灰透明**（非反色/渐变）；选中/hover 全家 = 一个墨色的 alpha 刻度 |
| **状态四元组** | success/destructive/warning 各有 `-foreground` + `-surface` + `-surface-foreground` 完整四件 | 状态色使用方式被 token 预先约束 |
| **节点类型色码** | file-node 蓝 / command-node slate / session-node teal / skill-node 紫 / subagent-node 橄榄(oklab) / plugin-node 琥珀 / idle-task 紫——每个 = `表面 alpha + foreground + hover` 三件 | 侧栏/文件树/任务列表的类型语义色系统 |
| **远程环境色码** | ssh=sky / wsl=green / docker=violet（surface/foreground/border 三件） | 环境身份可视编码 |
| **终端 ANSI 映射** | 16 色 terminal-* 全部映射回语义调色板（terminal-black=neutral-700，bright-white=neutral-950——浅色下反转） | 终端色不逃逸出体系 |
| **轨迹五色** | trajectory-user 蓝 / assistant teal / reasoning 紫 / tool-call 琥珀 / tool-result 天蓝 | 回合时间线的角色色 |
| **上下文七段** | context-breakdown-1..7（sky 递淡） | 上下文占用可视化的专属 ramp |
| **图表六色** | usage-chart-1..6 | 图表系列色 token 化（ZCode 版 viz 层） |
| **diff/git 色码** | diff-added/removed(+foreground)、git-added/deleted/modified/renamed/untracked/ignored/descendant/none | review 面的完整色词汇 |

### 2.3 密度与字号（一个被低估的设计）

- `--ui-font-size: 14px` 基准 + **calc 派生刻度**：`ui-2xs = base-5px`、`ui-xs = -4px`、
  `ui-sm = -2px`、`ui-caption = -1px`、`ui-lg = +2px`、`ui-xl = +4px`、
  `settings-title = +10/+16px`——**UI 字号与内容字号（text-sm/base/lg）解耦**，
  一处调 `--ui-font-size` 全应用密度随之变。
- 存在 `compact-web` 密度类与 `:root:not(.platform-mac)` 平台条件密度。
- 行高成对定义（`--text-sm--line-height: calc(1.25/.875)`）——v4 的字号-行高绑定。

### 2.4 动效令牌

- 时长刻度：`--wf-t-fast .12s / t-base .16s / t-enter .2s / t-ink .32s`、
  `--zcode-enter .26s / reveal .3s`、默认 transition `.15s`。
- 54 个 keyframes **全部功能命名**（tool-call-progress-indeterminate /
  task-search-result-highlight / zcode-stream-text-in / turn-navigator-enter /
  fork-highlight-pulse / reaction-particles），零装饰性命名。
- spring 曲线 `cubic-bezier(0.34,1.56,0.64,1)` 以任意值出现（入口 pop）。
- **reduced-motion 块 ×20**（AgentNodes：1）；`@media(hover:none)`、
  `supports-backdrop-filter` 渐进降级（玻璃效果全部带 fallback guard）。

### 2.5 暗色机制

`.dark` 类（276 处）+ `.theme-zai-light/.theme-zai-dark` 品牌主题双集
（152/151 变量）——**主题 = token 集整体换装**；`prefers-color-scheme` 仅 3 处
兜底。启动画面独立防闪门：`body.zcode-startup-ready` 门控 opacity 过渡 +
手调 spring pop 关键帧（0.72s，38%/58%/76% 三段回弹），且有中文修复注释
（首屏 logo 壳固定深色底、边框不随主题 token——首帧一致性原则）。

## §3 组件与交互词汇（bundle 计数实测）

| 词汇 | ZCode | AgentNodes | 差距含义 |
|---|---|---|---|
| workspace | 6104+1374 | — | 工作区是一等概念 |
| Session | 2028+1869 | （session 少） | 会话历史一等 |
| composer | 472 | 72 | 输入区是重点打磨对象（thought-bubble/thought-slider 计划思考可视化） |
| Subagent | 473 | 6 | 子代理可视化一等（node 色码 + 投影卡片） |
| diff/review | 1044/943 | 有 Review 面板 | diff 词汇完整（token + worker） |
| Plan | 980 | 29 | codingPlanWebview 独立面 |
| queue | 328 | 261 | 消息排队 UI 双方都有 |
| Permission | 106 | 72 | 审批 + CUA 独立面板 |
| ToolCall | 246 | 121 | 工具调用卡片 |
| checkpoint / TurnNavigator | 30/2 | **0** | **回合导航/checkpoint（rewind/fork）UI AgentNodes 完全没有**——§8.1 会话日志投影的直接 UI 证据 |
| reasoning | 213 | thinking 少 | 推理流展示一等 |
| gen-ui | 16（沙箱 640KB） | HtmlBlock 44 行 + JsonActionCard 392 行 | ZCode = 沙箱运行时 + vendor 库；AgentNodes = 内联原型 |
| Skill | 21 | 0 | 技能面 |
| Terminal | 177 | 0 | 内嵌终端（xterm+node-pty） |

## §4 核心设计哲学（四条，全部有证据）

1. **「中性承重、颜色载义」**：primary 是黑（neutral-950），品牌天蓝只出现在
   图标/链接/focus 等语义位置；用户气泡是 5% 黑透明而非反色气泡。颜色永远
   传达「这是什么类型/什么状态/什么环境」，从不传达「这很重要所以花哨」。
2. **「一个墨色 + 透明度 = 全部层级」**：surface/selected/hover/bubble 全部是
   `#0d0d0d` 的 3%-8% alpha——灰阶表面不需要另外的灰色 ramp，墨色透明度
   天然随背景自适应（这也是浅暗两套主题只需换 token 集不需换手法的理由）。
3. **「命名空间治理」**：情绪与特性色被封死在 `highspeed-*`（紫 `#6041ff` 渐变/
   辉光/粒子全在里面）、`wf-*`（mascot 表情动画）、`zcode-*` 里；基础骨架
   永远安静。**ZCode 的 built CSS 里 linear-gradient 有 138 处，但基础骨架
   一处都没有**——计数不是关键，隔离才是。
4. **「密度与层级是系统参数，不是逐处手调」**：--ui-font-size 一个旋钮 +
   calc 派生刻度 + compact-web 类 + 文本三级 token + 行高成对定义。reduced-motion
   20 处、hover:none、supports-guarded——**可访问性与降级是默认动作**。

## §5 逐维度对比矩阵（ZCode v3.18.2 vs AgentNodes webui @ui-workbench）

| # | 维度 | ZCode | AgentNodes | 判定 |
|---|---|---|---|---|
| 1 | 宿主形态 | Electron 多进程（main/host/scheduler/extension/sandbox）+ JSON-RPC/WS | Vite SPA + FastAPI SSE（EventSource×66） | 不抄形态；借鉴「渲染层=投影」的权威划分 |
| 2 | 框架 | React 19.2.4 | React 19.2.7 | 同 |
| 3 | CSS 引擎 | Tailwind **v4**（oklch/lightningcss/@theme） | **v3.4.19 + @tailwindcss/vite 4.3.3 混装**（v3 语法 + postcss/autoprefixer） | **简化点**：v4 迁移，顺带删 postcss/autoprefixer/shim |
| 4 | 组件基座 | radix + shadcn data-slot 约定 + cmdk/vaul | radix ×4（dialog/popover/slot/tabs），无约定 | 借鉴 data-slot 命名约定 |
| 5 | 状态 | Context×79 + useSyncExternalStore×46 + immer，**无外部状态库** | zustand×16 stores + immer | **不抄**——zustand 面积更小；ZCode 方案是产品规模下的选择 |
| 6 | 路由 | 无（tab 导航） | react-router v7（17 路由） | 不抄（web 场景 URL 有价值）；借鉴 tab 式工作区导航心智 |
| 7 | 令牌总量 | 569 去重（四层） | 55（半层） | 借鉴分层与语义角色（§6） |
| 8 | 主色策略 | **黑 primary + sky brand 双轨** | 紫 primary（light 翻橙） | **U-A 修订**：双轨取代「单强调蓝」 |
| 9 | 文本层级 | foreground/subtle/subtlest 三级 | muted-foreground 一级 | 借鉴三级命名 |
| 10 | 表面系统 | 墨色 alpha（3-8%） | 实色灰阶 + 渐变/玻璃 | 借鉴 alpha-on-ink |
| 11 | 状态色 | 四元组（color/fg/surface/surface-fg） | --success/--warning 两个孤立值 | 借鉴四元组 |
| 12 | 特性隔离 | highspeed-*/wf-* 命名空间 | 渐变/玻璃/aurora 散布基础层 | **正是 U-A 禁用清单要修的病** |
| 13 | 字号体系 | --ui-font-size 旋钮 + calc 刻度 + 成对行高 | text-* 直接用 + 任意值 9/10/11px | 借鉴密度旋钮 |
| 14 | 字体 | 系统栈（零自带） | Geist Variable 自托管 ×5 woff2 + JetBrains Mono | **简化候选**：去 Geist 用系统栈（决策点） |
| 15 | 动效 | framer-motion + 54 功能 keyframes + 时长 token + reduced-motion×20 | 纯 CSS 6 keyframes + reduced-motion×1 | 借鉴时长 token 与 reduced-motion 覆盖；不引入 framer-motion |
| 16 | 图表色 | usage-chart-1..6 token | 60 处硬编码 hex | 证实 U-A viz 层方案 |
| 17 | Markdown/富文本 | streamdown + KaTeX + mermaid + shiki | react-markdown + remark-gfm + syntax-highlighter | 暂不追（streamdown 可在 U-D 评估） |
| 18 | 虚拟化 | virtua | react-virtuoso | 不动（等价） |
| 19 | 终端 | xterm + node-pty + ANSI token 映射 | 无 | 远期；token 映射模式先记档 |
| 20 | 审批面 | Permission×106 + CUA 独立 preload 面 | 4 个互不相连的审批 UI | **U-B 直接证据**：统一面 + 高危动作物理隔离 |
| 21 | 回合控制 | checkpoint×30 + TurnNavigator + fork UI | 无 | **U-D 直接证据**：投影原则的 UI 配套 |
| 22 | Gen-UI | 沙箱运行时（640KB + vendor lucide/d3）+ MCP ext-apps 2.0 | HtmlBlock/JsonActionCard 内联原型 | U-E 时借鉴「沙箱 + 最小 vendor」形态；现在不建 |
| 23 | i18n | react-intl 全量（2.3MB 消息） | 无 | 不抄（范围决策） |
| 24 | 启动体验 | startup-ready 防闪门 + 手调 spring | 无 | 小成本借鉴 |
| 25 | 视觉回归 | —（未见基线机制证据） | 50 基线 PNG + catalog story + visual_diff | **AgentNodes 反超项**，保持 |

## §6 借鉴清单（→ U-A 修订建议）

1. **双轨主色**（黑 primary + 蓝 brand）取代「单强调蓝」——CTA 中性化后，
   强调色出现频率自然下降，禁多 CTA 落地更容易。
2. **文本三级**：`--foreground / --foreground-subtle / --foreground-subtlest`
   取代 muted-foreground 单级；shim 里 6 档 text-slate-* 正好收敛到 3 档。
3. **alpha-on-ink 表面**：selected/hover/用户气泡 = `color-mix(ink 3-8%)`；
   渐变气泡直接消失（禁用清单自动达成）。
4. **状态四元组**：success/warning/destructive 各配 surface/surface-foreground，
   U-B 审批面直接消费。
5. **UI 字号旋钮**：`--ui-font-size: 14px` + calc 刻度，监控/DAG 等高密度页
   直接受益；任意值 9/10/11px 问题被刻度吸收。
6. **Tailwind v4 @theme 迁移**：语义变量直出工具类，**slate-remap shim 与
   postcss/autoprefixer 一并退役**（U-A B 批的机制升级）。
7. **diff/status token 预留**：git-*/diff-* 词汇表为 review 面板做准备。
8. **data-slot 命名约定**：组件库演进的方向约定，先立约定后扩组件。
9. **checkpoint/turn-navigator 词汇**：U-D（会话日志投影）的 UI 配套名词表。
10. **startup-ready 防闪门**：index.html 内联关键样式 + ready 类门控。

## §7 简化清单（明确不抄）

1. Context×79 状态架构——zustand×16 保持，面积小、SSE 写入面清晰。
2. react-intl 全量 i18n——产品现状中文单语，2.3MB 消息包不值。
3. Gen-UI 沙箱运行时——U-E 前不建；届时也只 vendor 最小组件集。
4. framer-motion——现有 CSS keyframes 够用；引入 = 新依赖 + 新心智。
5. xterm/node-pty 内嵌终端——远期特性，非本轮。
6. 多进程 preload 面——web 形态不适用；等价物是 AgentNodes 的 API 面（已有）。
7. 品牌主题双集（theme-zai-*）——双主题保留但单 token 集足够。

## §8 对 U-A 设计文档的具体修订建议（供决策，未改文档）

| U-A 原方案 | 修订建议 | 理由 |
|---|---|---|
| 单强调蓝（#3b82f6） | 黑 primary + 蓝 brand 双轨 | ZCode 实证；CTA 归中性后强调色频率自动下降 |
| L2 保留 muted-foreground | 三级文本层级 | 一次性收敛 slate 6 档 |
| 灰阶 ramp 实色 | alpha-on-ink 表面系统 | 灰阶语义化 + 双主题自适应 |
| slate-remap shim 保留 | **v4 @theme 迁移，shim 退役** | 同口径机制在 ZCode 已验证；省 postcss/autoprefixer |
| 验证 = catalog + visual 基线 | 不变 + 新增密度旋钮检查 | AgentNodes 反超项保持 |
| （新增） | --ui-font-size 旋钮 + 成对行高 | 高密度页与 9/10/11px 任意值的根治 |

## §9 信息局限

- bundle 为 minified 产物：组件树结构、props 契约、交互细节（如审批卡片的
  精确文案与流）不可恢复，本文以「词汇计数 + token 值 + CSS 行为」为证据面。
- `.theme-zai-light/dark` 双集的逐变量差异未逐条比对（152/151 变量）——
  对借鉴结论无影响（主题=换装机制已确认）。
- Gen-UI 沙箱的协议面（MCP ext-apps 如何驱动 UI）只有包名证据，未逆向协议。
