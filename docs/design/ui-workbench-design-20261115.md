# UI 工作台：设计语言主文档（变迁链 + 设计精髓 + 待讨论）

> 2026-11-15 · 分支 `ui-workbench` · 状态：**讨论稿（未实施，待 §6 逐项确认）**
> · 性质：UI 轨道主文档——记录从现状侦察到三视图示意图的完整变迁链，
> 固化设计精髓，承载未决事项；后续讨论以本文档为锚点续接。
> · 关联：`docs/ui_final_turn_001.md`（竞品预研）· `docs/ui-zcode-source-research-20261114.md`
> （ZCode 源码级逆向 §1-§10）· `docs/design/ui-a-token-system-design-20261114.md`
> （U-A 方案 v1，含 §9 五决策点）· `tmp/mockup/agentnodes-ui-mockup.html`（三视图示意图）

## §0 文档定位

1. UI 轨道拆分为 U-A（令牌）→ U-B（审批）→ U-C（运行任务）→ U-D（会话日志投影）
   → U-E（Gen-UI），本文档是它们共同的上位文档：令牌终稿 + 三表面蓝图 + 变迁史。
2. U-A 设计文档（v1）保持不动；其修订（v2 方向）记录在本文 §3/§5，待确认后
   才回写 U-A 文档再开工——**结构与价值观分离，文档同样适用**。
3. 所有实测数字可追溯到 probe 脚本（tmp/probe/*，gitignore）与两次截图核对。

## §1 变迁链（每步：输入 → 动作 → 产出 → 改变了什么）

### Step 0 · 现状侦察（2026-11-14，branch ui-workbench 创建后）

- **输入**：用户「开启新分支做 UI 相关工作」；竞品预研文档已入档（70e665cf）。
- **动作**：webui 全量侦察（webui_recon / fe_deep / token_inventory / ua_blast_radius
  四个 probe）。
- **实测**：React 19 + TS + Vite + zustand×16 + radix×4 + router v7 + react-virtuoso；
  48,659 行 / 172 tsx；globals.css 653 行、**106 处定义 / 55 去重 / 15 死变量**；
  情绪层 = 渐变令牌×5 + 玻璃三类 + shadow-glow + aurora 四层 + conic focus ring；
  组件消费令牌主通道 = **slate-remap shim（~70 行 color-mix）**；情绪层直接消费面
  glass×10 / aurora×5 / glow×7 / gradient×8 / **amber×41 文件**；硬编码色 109 处
  （图表四件套 ~60）；任意值 px 9/10/11 三档并存；验证锚点 50 基线 PNG + catalog。
- **关键转折**：推翻开工前「收敛 55 个散落变量」的判断——现状令牌体系与预研
  §8.2 目标（中性灰阶 + 单强调 + 禁渐变/玻璃/辉光）**逐项正面对撞**，globals.css
  里甚至有注释叫「Visual upgrade layer」。**U-A 是设计语言转向，不是技术清理。**
- **产出**：U-A 设计文档 v1（R-014）：三层令牌架构 + A 骨架批（零视觉）→
  B 转向批（唯一价值观 commit）→ B-2 amber 批 → C 卫生批（hygiene 门）的工序；
  §9 五决策点待裁。

### Step 1 · ZCode 源码级逆向·第一轮（用户指示「对比这个 agent 的 UI」）

- **输入**：用户要求对比当前在用的 agent（ZCode）UI，调研框架选择、UI 设计、
  核心哲学，逐细节对比。
- **动作**：本机安装的 ZCode Desktop v3.18.2 的 app.asar（367MB / 31,961 文件）
  自校验解包；渲染层 CSS 令牌普查（492KB 构建产物）+ 入口 bundle 签名扫描
  （7.0MB）+ CLI 内核指纹（zcode.cjs 14.9MB，ink×1562 = Ink TUI）+ AgentNodes
  同口径镜像统计。
- **发现**：①核心哲学 = **中性仪表板**（primary=黑 CTA、brand=sky-400 单强调、
  用户气泡=墨 5% alpha、渐变 138 处全锁在 highspeed-*/wf-* 命名空间、
  --ui-font-size 密度旋钮、reduced-motion×20）；②栈 = React 19 + Tailwind **v4**
  （oklch/@theme/lightningcss）+ radix + Context×79/useSyncExternalStore×46
  （无 zustand、无路由）+ virtua/xterm/shiki/cytoscape + JSON-RPC/WS 多进程；
  ③文本三级（foreground/subtle/subtlest）、状态四件套、节点/远程/终端/diff
  全套功能色词汇；④简化项：系统字体零自带、Context 状态架构不抄、react-intl
  2.3MB 不抄。
- **产出**：`docs/ui-zcode-source-research-20261114.md`（25 维对比矩阵、借鉴 10 条、
  简化 7 条、§8 U-A 修订建议表）。**入档 commit `5d7e89b8`。**
- **改变了什么**：U-A 的「单强调蓝」升级为「**黑 primary + 蓝 brand 双轨**」；
  新增「**Tailwind v4 @theme 迁移 = shim/postcss/autoprefixer 一并退役**」；
  muted-foreground 单级 → 文本三级；灰阶实色 → alpha-on-ink。

### Step 2 · ZCode 逆向·第二轮（用户「再次调研一下…确认完毕后才能开始」）

- **输入**：用户确认第一轮入档，要求继续调研、确认前不开工。
- **动作**：renderer 全量 JS 抽取（2,776 文件 / 42MB）逐面扫描 + HTML 面
  （visualize/cua-permission-panel/resource-manager）直读 + 协议 chunk 与 host
  bundle 双向取证。
- **发现**：①**Gen-UI 双载体**：inline = 声明式契约 HTML（data-tooltip /
  role=tablist / .viz-carousel）+ 宿主行为升级脚本；sandbox = MCP Apps
  （ext-apps 2.0）完整方法面（ui/initialize、ui/message、ui/request-display-mode
  「host decides border」、callTool、sampling.tools）；②**宿主令牌寄生实现级确认**：
  visualize.css 引用宿主变量 94 个 + 自带 79 个 fallback，0 渐变 0 玻璃
  0 prefers-color-scheme——主题完全由宿主注入；③**四档审批粒度**
  （allowOnce/allowAlways/rejectOnce/rejectAlways + permission.* 事件形状 +
  setPermissionMode）；④**steer 协议事件**（turn.steerQueued/steerDrained）与
  fork 全套（forkSession/forkAssistant/forkToWorktree/checkpointRestored）；⑤
  回合控制词汇 undo×491/timeline×359/fork×241/rewind×211/checkpoint×93/steer×35；
  ⑥subagentsStore 状态形状（乐观更新/未读/boards/elicitation）；⑦修正：
  data-slot 全 renderer 仅 7 个（非整包 shadcn，自研 + radix 裸用）。
- **产出**：research doc §9 局限更新 + §10（六小节）。
- **改变了什么**：U-B 拿到审批事件形状与四档粒度；U-D 拿到 steer 队列语义与
  fork/checkpoint 词汇表；U-E 拿到双载体 + 寄生的实现模板。

### Step 3 · 三视图示意图（用户「画一个示意图我看看界面长啥样子」）

- **输入**：两轮调研 + U-A v1；用户要看界面长什么样。
- **动作**：单文件高保真 mockup（tmp/mockup/agentnodes-ui-mockup.html，纯 HTML/CSS，
  零依赖），浏览器实机核对两轮（浅色 + 深色 + 标注），修复 4 处
  （`.turnctl span` 覆盖 pin 的 display、composer 固定在滚动区外、
  v-chat 嵌套闭合遗漏、识别 fullPage 截图拼接伪影而非页面 bug）。
- **产出**：会话视图（用户气泡=墨 5%、三级文本、四档审批卡、steer 排队条、
  回退/分叉、inline 可视化、子代理面板、上下文分段条）+ 双主题验证。
- **改变了什么**：黑 primary/蓝 brand/三级文本/alpha-on-ink 从「建议」变成
  **看得见的实物**；用户反馈「设计的不错，非常好」——方向获得初步认可。

### Step 4 · 扩展 Trace / RSI 视图（用户「还需考虑 trace 以及 rsi 的」）

- **输入**：用户要求覆盖其余功能面，点名 trace 与 RSI。
- **动作**：同一文件加视图切换（会话/Trace/RSI 进化），补 CSS ~130 行 + 两个
  视图 DOM；浏览器逐视图截图核对（Trace 浅色、RSI 深色），修顶栏换行与
  失败轮次红点。
- **发现（验证性）**：同一套令牌撑起对话面/检视面/进化面，情绪色纪律跨面成立
  ——Trace 五色是「角色」、RSI 红黄绿是「健康度」，均非装饰。RSI 视图内容
  直接对应 goal-merge 设计的 UI 投影（四源 2/4 活跃、study.round_done 空缺
  画成虚线卡、LeverScoreboard Δ 色、闭环五阶段）。
- **产出**：三视图 mockup 终稿 + 图例 12 条 + token 芯片行。
- **改变了什么**：令牌先行的价值得到实证；Trace/RSI 成为 U-C/U-D 的视觉输入。

### Step 5 · 本文档（用户「将以上设计文档写入文档…后续还要继续讨论的」）

- **动作**：变迁链 + 精髓 + 待讨论清单固化为本文档。
- **状态**：实施未开始（用户明确：确认完毕后才开工）。

## §2 设计精髓（8 条，原则 → 证据 → AgentNodes 落法）

| # | 原则 | 证据（出处） | 落法 |
|---|---|---|---|
| 1 | **中性承重、颜色载义**：CTA 归中性黑，唯一情绪色只做语义标记 | ZCode `--color-primary: neutral-950` + `--color-brand: sky-400`；允许一次=黑按钮（§1 Step 1） | `--primary: #171717`（dark 反转白）+ `--brand: sky`；发送/新建/允许=黑，运行态/未读/链接/focus=蓝 |
| 2 | **一个墨色 + alpha = 全部层级** | ZCode surface #0d0d0d08 / selected 0d / bubble 0d0d0d0d；Grok Bot `--cursor-base` + **双 alpha 表**（53 键，light/dark 同键不同值）| surface/selected/hover = `rgb(var(--ink))` 3-8% alpha；**用户气泡分主题**：light=墨 100%（实心）/ dark=墨 18%（通透）——§11.2 |
| 3 | **文本三级显式成 token** | ZCode foreground/subtle/subtlest 三级命名；Grok Bot 四级 `text/secondary..quaternary` + `icon/primary..quaternary`；AgentNodes 现状 muted 一级 + slate 6 档散用 | `--fg / --fg-subtle / --fg-subtlest / --fg-quaternary` **四级**，shim 的 6 档收敛到 4 档（§11.5） |
| 4 | **特性命名空间治理**：情绪封死在命名空间内，基础骨架永安静 | ZCode 渐变 138 处全在 highspeed-*/wf-*；AgentNodes 自有 wf-*/dag-* 先例；Grok Bot `glass.scss/glass.stylex` 独立 @layer + `data-cursor-glass-mode` 运行时开关（§11.3） | 多色渐变/辉光/aurora 拆除；**玻璃降为可关层**（`data-*` 开关 + 刻度化 blur）；hygiene 门按 mask/垫片/发丝线白名单判定（§11.4） |
| 5 | **密度是系统参数** | ZCode `--ui-font-size: 14px` + calc 派生刻度 + compact-web | `--ui-font-size` 旋钮 + 成对行高；9/10/11px 任意值被刻度吸收 |
| 6 | **状态四件套** | ZCode success/destructive/warning 各 color/fg/surface/surface-foreground | 审批卡（warning-surface）、通过（success）、失败（danger）直接消费 |
| 7 | **渲染层是投影**：状态可从事件日志推导，否则 rewind/fork 是谎言 | ZCode JSON-RPC/WS + steerQueued/steerDrained + checkpointRestored；调研 §8.1 | SSE→store 单向流；steer 排队、回合时间线、span 级回退（U-D） |
| 8 | **Gen-UI 双载体 + 宿主令牌寄生** | ZCode inline 契约 HTML + MCP Apps 沙箱；visualize.css 94 宿主引用 / 0 主题内嵌（§10.1） | inline=声明式契约+行为脚本；canvas=沙箱；生成片段自带 fallback + var(宿主) 覆盖 |

## §3 目标令牌体系（修订终稿 = U-A v2 方向，待 §6 确认后回写 U-A 文档）

```
L0 原语层   灰阶 ramp（双主题）· --brand: sky-500/400 · --ink（墨色基）
            alpha 语义表 ×2（light/dark 同键不同 alpha，§11.2）
            specular 高光族 --spec-1..4（§11.6）
            轨迹五色 #2563eb/#0f766e/#7c3aed/#d97706/#0284c7（user/assistant/
            reasoning/tool-call/tool-result，ZCode trajectory 词汇）
            viz 系列色 --viz-1..8（图表，豁免单强调）
L1 语义层   保留现命名契约（组件零改动）：background/foreground(+subtle/
            subtlest/quaternary)/card/popover/muted/border/input/sidebar
            + primary（=黑）+ brand + 状态四件套 ×3（每组 10 token，§11.7）
L2 组件层   canvas/wf/dag 令牌收编为 L1 派生
机制        Tailwind v4 @theme 迁移：语义变量直出工具类，slate-remap shim、
            postcss、autoprefixer 退役；字体=系统栈（Geist 去留见 §6.5）
刻度        间距 4px 基数 + 0.25 细分（40 档）· 圆角 10 档 · 字号 11/12/13/14/15
            · 行高 14/16/18/22/24 · 控件高度 20/24/28/32（§11.8）
```

主题值锚点（示意已验证）：light bg #fafafa / dark bg #262626；ink light=黑
dark=白；primary light #171717 / dark #ffffff（反转让 CTA 恒中性）。

## §4 三表面蓝图（同一骨架的三种投影）

| 面 | 三栏分工 | 专属词汇 | 颜色纪律 |
|---|---|---|---|
| **会话**（默认） | 会话列表 / 消息流+composer / 工作台（审批+子代理+todo） | 四档审批、steer 排队、回退/分叉、inline 可视化 | brand=运行态/未读/链接；气泡=墨 5% |
| **Trace**（检视） | 回合时间线（◆/⑂） / span 时间线+指标+过滤 / span 详情+注入器时序 | 轨迹五色 span、checkpoint 回退、U6-2 时序验收 | 五色=角色（圆点+徽标），其余全中性 |
| **RSI**（进化） | 进化轮次 / 信号源健康+LeverScoreboard+闭环阶段 / 证据流 journal | 事件 kind mono 徽标、空缺=虚线卡、Δ 色 | 红黄绿=健康度，分数 Δ=success/danger-fg |

## §5 工序（修订后，待 §6.6 确认节奏）

| 批 | 内容 | 验证 |
|---|---|---|
| **A-0 基线补齐**（§10.2 新增，零代码变化） | catalog/visual 测试参数化双主题，dark+light 各录一套（预计 ~84 基线） | 新基线全绿 = light 盲区关闭 |
| A 骨架 | v4 @theme 迁移 + L0/L1/L2 重排 + shim/postcss/autoprefixer 退役 + 死变量 15 清除 + 轨迹/viz 色层立占位（值不动）+ **刻度表落地**（§11.8 五张表）+ **alpha 表骨架**（§11.2 两套表，先只填现值） | pixel diff = 0（**双主题基线**）+ vitest |
| B 转向 | palette violet→中性+蓝 brand、L0/L1 落值、多色渐变/辉光/aurora 拆除、情绪层 ~25 文件改写、**玻璃降为可关层**（§11.3） | 基线重录（人审点）+ e2e lane |
| B-2 amber | 40 文件机械映射到 **--warning 10 token 组**（§11.7）+ StatusBadge 组件化（§10.1：实测全为状态语义） | 同上 |
| C 卫生 | viz 收编 ~60 + 散 hex ~49 + test_token_hygiene 门（**三条件判定**，§11.4：多色 & 非 mask & 非单色垫片） | hygiene 门 + 全绿 |

## §6 待讨论（后续讨论清单——每条含推荐与备选）

| # | 事项 | 推荐 | 备选 |
|---|---|---|---|
| 6.1 | **转向确认（价值观，前置）** | 执行中性化转向（§2 全部落图） | 保留紫橙 identity，U-A 退化为技术清理 |
| 6.2 | amber×41 处置深度 | 状态语义保留（走令牌）+ 装饰收敛灰阶 | 全部灰阶化（状态信息受损） |
| 6.3 | 双主题去留 | 保留 dark+light 都中性化（theme store 是既有功能） | 只留 dark（删功能超范围） |
| 6.4 | aurora 替代物 | 纯色 + 保留细网格层 | 完全纯色 |
| 6.5 | Geist 字体去留 | 换系统栈（ZCode 实证零自带可行，示意图即效果） | 保留 Geist（品牌选择，+5 woff2） |
| 6.6 | 工序节奏 | A→B→B-2→C 连续，每批一 commit + 每批验证收口 | 一批一确认 |
| 6.7 | Trace/RSI 视图定位 | 作为 U-C/U-D 的设计输入存档（不进本轮实施） | 提前纳入本轮范围 |
| 6.8 | 入档 commit 范围 | 本文档 + research §10 + mockup 一并入档 | 只入本文档 |
| 6.9 | **UI 先行灰态 vs 等后端**（§9.3 评审新增） | 三表面按「可用/灰态/待后端」分级，U-B 四档审批等先灰态上线，不等 B2-② 解冻 | UI 轨道等待 kernel 侧能力同步 |
| 6.10 | **玻璃去留（§11.3 新增，待裁）** | 玻璃降为**独立可关层**（`data-*` 开关 + 刻度化 blur），默认关；保留 ZCode 式命名空间隔离 | 彻底拆除（Grok Bot 反例：玻璃本身不是罪，无条件玻璃才是）｜全部保留（违背中性仪表板方向） |

## §7 关联资产

| 资产 | 位置 | 状态 |
|---|---|---|
| 竞品预研 | docs/ui_final_turn_001.md | 已入档（70e665cf） |
| ZCode 逆向调研（含 §10 二轮） | docs/ui-zcode-source-research-20261114.md | §1-§9 已入档（5d7e89b8），§10 未提交 |
| **Grok Bot 源码级调研（§11 依据）** | docs/ui-grok-bot-source-research-20261116.md | 未提交（本文档 §11 引用其全部实测） |
| U-A 方案 v1 | docs/design/ui-a-token-system-design-20261114.md | 未提交（待 §6 确认后回写 v2） |
| 本文档 | docs/design/ui-workbench-design-20261115.md | 未提交 |
| 三视图示意图 | tmp/mockup/agentnodes-ui-mockup.html | 未入档（tmp 为 gitignore；如需入档移至 docs/mockups/） |
| probe 脚本群 | tmp/probe/* | gitignore，随用随弃 |

## §8 讨论时的既知事实备忘（防重新论证）

- shim 是机制不是债：v4 迁移后才退役；v3/v4 混装（tailwindcss 3.4.19 +
  @tailwindcss/vite 4.3.3）是迁移前置核查项。
- 基线重录 = 回归网一次性重置：重录 commit 必须附前后对照（人审点），
  hygiene 门同批补位。
- AgentNodes 反超项：视觉回归网（50 基线 + catalog）ZCode 源码未见等价物，保持。
- ZCode 简化项不抄清单：Context×79、react-intl、Gen-UI 沙箱（U-E 前不建）、
  framer-motion、内嵌终端、品牌主题双集。
- RSI 视图内容 = goal-merge 设计（docs/design/goal-capability-merge-design-20261105.md
  计划稿）的 UI 投影，四源/lever/事件 kind 均为真实词汇。

## §9 评审记录（第二轮，2026-11-15，挑刺视角；无推翻级发现）

### 9.1 可访问性硬伤 → 落为令牌纪律

- **fg-subtlest 不达 WCAG AA（实测计算）**：light `#a3a3a3` on `#fafafa` ≈ **2.4:1**
  （AA 正文需 4.5）；dark `#737373` on `#262626` ≈ **3.2:1**。subtle 档两主题达标
  （light #737373 ≈ 4.55 ✓ / dark #a3a3a3 ≈ 6.0 ✓）——**只有 subtlest 一档有问题**。
- **处置：不改值，写死用途白名单**。subtlest 仅限纯装饰/占位符/禁用态
  （WCAG 对非文本装饰与禁用控件无最低对比要求），**禁止承载信息**；信息性
  三级文本一律 subtle。进 U-A v2 纪律 + review checklist（信息性判断无法静态
  lint，人工门）。
- 状态 Δ 已带 +/- 符号方向（色盲辅助）✓，示意图即如此。

### 9.2 技术风险评审

- **v4 迁移真相核实（本轮评审最重要发现）**：`vite.config.ts` 仅挂 `react()`；
  `postcss.config.js` = tailwindcss + autoprefixer。**`@tailwindcss/vite ^4.3.3`
  是从未挂载的死依赖**，现网 = 纯 v3。迁移路径 = 挂载插件 + 删
  postcss.config.js/autoprefixer + `@tailwind` 指令改 `@import "tailwindcss"` +
  **`@custom-variant dark` 绑定 `[data-theme='dark']`**（必须保留手动切换机制，
  跟随 prefers-color-scheme 会破坏 ThemeStore）。死依赖清理成为 A 批顺带收益。
- **A 批「pixel diff = 0」承诺改写**：50 基线 ≠ 全应用。承诺应为
  **「基线覆盖面内零 diff」** + v4 preflight 差异可能影响基线外页面 → A 批后
  加 17 路由中 5 个高频页的人工巡检清单。
- B 批波及 vitest 断言（如 MessageBubble.test 断言 gradient 类）——U-A v1 §6.5
  已列，维持。

### 9.3 依赖链评审（本轮最大跨轨风险：UI 词汇先行于后端能力）

| 表面能力 | 后端现状 | 处置建议 |
|---|---|---|
| 四档审批 / permission mode | gateway 事件形状未核实（B2-② 冻结中） | UI 先行=**灰态**（按钮渲染、行为置灰），或等 B2-② 解冻同步扩（→ §6.9） |
| steer 队列 / checkpoint / fork | kernel 无对应能力（预算只剩 BudgetDecider，无 checkpoint API） | 同上，U-D 灰态设计先行 |
| RSI 视图 | 依赖 goal 能力恢复（goal-merge R1）+ LeverScoreboard 为 archive 历史资产 | 上线即展示「2/4 健康」的静态投影——**缺口可见化是特性不是缺陷** |
| Trace 视图 | span 级 token/cost/时序字段需 kernel 事件载荷携带，现状未盘点 | 前置一次 SSE 载荷字段普查（半小时级 probe） |

### 9.4 流程评审

- 示意图在 tmp/（gitignore）→ 建议移 `docs/mockups/ui-mockup-20261115.html`
  入档（并入 §6.8 范围）。
- **单一事实源边界**：主文档 = 哲学/蓝图/变迁/待决；U-A = 实施（值表/工序/
  验证/门）。回写 v2 时工序细节只留 U-A，本文 §5 改为引用，防双头维护。
- **amber 普查前置**：41 文件中 warning 语义 vs 装饰占比未盘点——B-2 开工前
  必须先出分布表（半小时级），否则处置标准悬空。
- 基线重录人审点补操作定义：重录 commit 附 50 基线前后对照，逐张标注有意
  变化，**无标注的 diff = 验收失败**。

### 9.5 评审结论

1. 方向成立（中性仪表板 + 双轨主色 + 三表面同骨架），无需返工级修改。
2. U-A v2 回写时并入 7 个修订点：① subtlest 用途白名单 ② A 批承诺改写 +
   页面巡检清单 ③ @custom-variant dark 绑定 ④ 死依赖清理入 A 批 ⑤ 三表面
   「可用/灰态/待后端」分级表 ⑥ amber 普查列为 B-2 前置 ⑦ 基线重录人审
   操作定义。
3. 新增待决项 §6.9（UI 先行灰态 vs 等后端），影响 U-B 排期。

## §10 深度评审（第三轮，2026-11-16，实测驱动——四个悬空假设全部落地）

### 10.1 实测数据（deep_review_probe.py）

| # | 悬空假设 | 实测 | 结论 |
|---|---|---|---|
| 1 | Pillow 是否可用（A 批 pixel 验证网有效性前提） | **12.3.0 可用**，降级分支不会触发 | A 批验证网有效 |
| 2 | 基线主题覆盖 | **47 基线全录于默认主题（dark）：0 light 命名、测试无 data-theme/setTheme** | **light 零回归覆盖**（重大，→ 10.2） |
| 3 | amber×41 的语义分布 | 144 命中/40 文件；主模式 = `bg-amber-500/10 + border-amber-500/30 + text-amber-400` **warning 三件套**（DAG/Monitor/ToolCallBlock/RoundHistory/QueuePauseBanner/PermissionRequestDialog 全是状态位）；**装饰性 amber ≈ 0** | 6.2 从「处置决策」降为「令牌映射表」：text→--warning-fg、bg/10→--warning-surface、border/30→color-mix(warning 30%)；三件套重复 → 顺手组件化 StatusBadge |
| 4 | 三级文本迁移面 | **434 处** slate-500/400/600 → subtle/subtlest；303 处 slate-100/200/300 → fg；`text-muted-foreground` 组件**零使用**（全走 shim） | U-A v2 附**值级映射表**（300/500 的归档需逐档定）；迁移=机械替换 |
| 5 | catalog 覆盖面 | 34 story / 106 组件+页面 = **32%**；消息流/DAG/命令面板/空态等高频核心已覆盖 | 9.2 承诺改写成立 + 页面巡检维持 |
| 6 | Trace 数据底座 | kernel `cost`×321 / `prompt_tokens`×21 / `completion_tokens`×20 大量存在；前端 SSE 已消费 tokens（31 处）；事件类型 agent/text/tool_call/thinking | **数据在，缺口=事件载荷中间层映射**——比预想乐观，非从零建 |

### 10.2 改工序发现一：light 主题零基线 → 新增 A-0 批

light 的变化比 dark 更大（主色翻橙要消失），却没有一张 light 基线——中性化转向
在 light 下**不可验证**。新增 **A-0 基线补齐**（零代码变化）：catalog/visual
测试参数化双主题，预计 34+8 × 2 ≈ **84 基线**。A 批「零 diff」从此在双主题下
有效。§5 工序表已更新。

### 10.3 改工序发现二：B-2 从「处置决策」降为「机械映射」

amber 实测全为 warning 语义（144 处无装饰滥用）——6.2 的「深度」问题消失，
剩余工作 = 映射表执行 + StatusBadge 组件化（消灭 20+ 处三件套复制粘贴）。
工作量重估：从「最大单点」降为半天级。

### 10.4 交互深度评审（每个面问「满载时怎么办」）

| 面 | 满载场景 | 设计答案（进 U-C/D 设计时细化） |
|---|---|---|
| 右栏工作台 | 审批 >5、子代理 >8 | 审批折叠为计数 + 第一条展开；子代理分组折叠 |
| steer 排队 | 多条排队、编辑/撤回 | 排队列表 + 每条可编辑/撤回（U-D 词汇，ZCode queue×328 前例） |
| Trace | 几百 span 的长 run | 按 tool 名聚合折叠 + virtuoso 虚拟化 + 失败 span 快速跳转 |
| RSI 记分板 | 分数语义不自解释 | hover tooltip：证据链 N 条 · 最近证据摘要 |

### 10.5 哲学自批判

1. **借的是纪律不是配色**：brand 蓝是候选值非品牌决策；双轨主色的好处 =
   换品牌色只动 `--brand` 一个变量，体系不动（6.1 的深层意义）。
2. **三栏惯性检查**：三个面的三栏均为真实角色划分（导航/流/详情），非照搬；
   但 RSI 中栏三块密度偏高 → U-C/D 设计时可拆 tab。
3. **密度默认值**：AgentNodes 对话阅读型 vs ZCode IDE 型——默认 14px 但暴露
   密度设置（13/14/15 三档），不照抄 IDE 密度。
4. **中文渲染**：subtle 文本 + alpha 表面叠加的极端组合未实测 → A 批后补一帧。

### 10.6 工程面备忘

- color-mix/oklch 浏览器基线 = Safari 16.2+ / Firefox 113+（2023）——webui
  目标浏览器清单需确认（旧 Safari → 决策项）。
- CSS 体积：v4 @theme 变量按需 emit，ZCode 492KB（含全部工具类）为参照上限。
- className 断言迁移：434（文本层级）+144（amber）处替换会波及部分 vitest
  断言，B/B-2 批随改随验。

### 10.7 评审结论（三轮累计）

- 方向三轮评审均成立，无推翻级发现；实测把 2 个「决策项」降级为「执行项」
  （6.2 amber、三级文本映射）。
- U-A v2 修订点 7 → **9 个**：+⑧ A-0 双主题基线参数化 ⑨ slate→三级值级映射表。
- 工序 4 批 → **5 批**（+A-0）。
- §6 待决：6.2 实质已被实测回答（映射表是唯一合理解，待形式确认）；6.9
  仍卡 U-B 排期；6.1 转向确认仍是总前置。

## §11 Grok Bot 源码级对照（第四轮，2026-11-16）

> 证据来源：Grok Bot Desktop v1.1.0（Cursor/Anysphere）自校验解包，
> `resources/app.asar` 57MB → 645 文件；CSS 822KB / 603 JS chunk。
> 全文见 `docs/ui-grok-bot-source-research-20261116.md`（以下简称 GB 调研）。
> **本轮性质：把前三轮的"建议"换成"有人真这么做了"的实证，并推翻 2 条既有判断。**

### 11.1 本轮改变了什么（四条）

| # | 原判断 | 实测 | 处置 |
|---|---|---|---|
| 1 | 用户气泡 = 墨 5%（精髓#2） | light=**100% 实心** / dark=**18% 通透** | 改 §2 精髓#2 + §3 L1（§11.2） |
| 2 | 渐变/玻璃/辉光一律拆除（精髓#4） | Grok Bot 玻璃是**独立可关层**；232 处 gradient 里真装饰渐变**仅 1 处且是死代码** | 改 §2 精髓#4 + hygiene 门定义（§11.3/11.4） |
| 3 | 文本三级（ZCode 口径） | Grok Bot 四级 `text/secondary..quaternary` | 改 §2 精髓#3 + §3 L1（§11.5） |
| 4 | 无 specular 概念 | `--spec-1..4` = `#ffffff1f/08/05/03`，dark 用高光代替阴影 | §3 L0 新增一族（§11.6） |

### 11.2 双 alpha 表（推翻"单一 alpha 双主题通用"）

Grok Bot 的 `:root` 只有一个墨色基 `--cursor-base`，但真正驱动主题的是
**JS 里两张 53 键、键集完全相同、值不同的 alpha 表**。关键差异：

| 语义位 | light | dark | 含义 |
|---|---|---|---|
| `fill/bubble-user` | **100** | **18** | light 实心墨气泡；dark 才半透 |
| `specular/primary` | **56** | **12** | light 用强高光，dark 只用微光 |
| `shadow/control` | **12** | **0** | **dark 完全不用阴影分层** |
| `shadow/window/ambient` | **56** | **0** | 同上 |
| `bg/scrim` | 50 | 69.7 | 遮罩在暗底要更重 |
| `text/secondary` | 68 | 66 | 文本层基本同档 |

**纪律**：同一语义位在 light/dark 允许且**应当**有不同 alpha；禁止"一个 alpha 打两主题"。
`shadow/*` 与 `specular/*` 是**互补对**——dark 用 spec、light 用 shadow。

### 11.3 玻璃=可关层，不是罪（修正精髓#4）

```
@layer anysphere.tokens, anysphere.scss, anysphere.stylex,
       glass.scss, glass.stylex, overrides
       ↑ 玻璃是独立 @layer，层级上永远低于 overrides
body[data-cursor-glass-mode=true]   ← 运行时开关（17 处 CSS 引用）
backdrop-filter: 21 处；blur 走 var(--cursor-spacing-N) 复用间距刻度
```

**修正后的纪律**：
1. 默认骨架**禁玻璃**（无条件玻璃是问题）；
2. 玻璃只能作为**独立可关层**存在，必须走 `data-*` 开关；
3. **blur 必须走刻度**（Grok Bot 实测七档 4/6/8/10/12/16/20，且多用
   `blur(var(--cursor-spacing-N))` 复用间距令牌），禁止 magic number。

### 11.4 hygiene 门必须三条件判定（原计划会误伤）

Grok Bot 232 处 `gradient` 逐条分类结果：

| 类别 | 数量 | 判定 |
|---|---|---|
| `mask-image` 渐隐/溢出淡出/conic 箭头 | **140** | **合法**（工程手法） |
| `linear-gradient(var(x),var(x))` 同色垫片 | **19** | **合法**（叠层技巧，非渐变） |
| 1px 发丝线（`transparent 44% → text 46-54% → transparent 56%`） | ~41 | **合法** |
| `radial-gradient` 点阵/纹理 | 7 | 合法（纹理，非装饰渐变） |
| `color-mix` 令牌混合 | 8 | 合法（数据驱动） |
| shimmer 微光 | 2 | 视语境（加载态可留） |
| **多色装饰渐变** | **1** | **违规**——且实测 `.ui-xio6eo` 在 603 个 chunk 里**零引用 = 死代码** |

**门定义改为**：`多色 gradient` **且** 非 `mask-image` **且** 非单色垫片 → 才告警。
按关键字一刀切会误伤 140+19+41 处合法用法。

### 11.5 文本四级（不是三级）

| 级 | Grok Bot token | light alpha | dark alpha | 用途 |
|---|---|---|---|---|
| 1 | `text/primary` | = base | = base | 正文/标题 |
| 2 | `text/secondary` | 68 | 66 | 次级信息 |
| 3 | `text/tertiary` | 56 | 54 | 辅助说明 |
| 4 | `text/quaternary` | 40 | 38 | **禁用/占位/纯装饰**（§9.1 白名单同款纪律） |

`icon/*` 独立四级（92/66/52/32 与 90/64/50/30）——**图标比文本更亮**，
因为细线条需要更高对比才不糊。AgentNodes 现状只有 muted 一级 + slate 6 档散用。

### 11.6 specular 高光族（新增）

```css
--spec-1: #ffffff1f   /* 12% 顶边高光/玻璃边 */
--spec-2: #ffffff08   /*  3% 次级 */
--spec-3: #ffffff05   /*  2% 三级 */
--spec-4: #ffffff03   /*  1% 极微 */
```

这是 AgentNodes 完全没有的词汇。现状用 `shadow-glow` 表达"抬起"，但
**glow 是发光、specular 是反光**——后者才符合中性仪表板。light 主题下
specular 应为深色系（Grok Bot light 用 56/16/12/6，方向相反）。

### 11.7 状态色 = 10 token 组（不是单色）

Grok Bot 每个状态/角色色都是完整一组：

```
roleFill / roleFillHover / roleFillPressed / roleFillBright
roleForeground / roleForegroundHover / roleForegroundBright
+ role（裸色）/ roleQuaternary（surface 档）
```

对照 AgentNodes amber 处置：B-2 批实测 144 处全为 warning 语义，但**各写各的
`bg-amber-500/10 + border-amber-500/30 + text-amber-400` 三件套**。若只做
"三件套 → StatusBadge"，只是把复制粘贴收进组件；**令牌侧必须定义成组**，
否则 hover/pressed/disabled 态无处取值。

### 11.8 刻度表（直接采用）

```
间距  40 档：0-25=1 / 0-5=2 / 0-75=3 / 1=4 … 1-75=7 / 2=8 … 2-75=11 /
      3=12 … 3-75=15 / 4=16 … 20=80（12px 后每 4px 一档）
圆角  10 档：none 0 / xs 2 / sm 4 / base 6 / lg 8 / xl 12 / 2xl 14 / 3xl 16 / 4xl 18 / full 9999
字号  5 档：xs 11 / sm 12 / base 13 / lg 14 / transcript 15
行高  5 档：xs 14 / sm 16 / base 18 / lg 22 / transcript 24
控件  4 档：sm 20 / md 24 / lg 28 / xl 32
动效  8 档：instant 50ms / fast .1s / normal .15s / slow .2s / slower .3s / drawer .5s
说话区 min(640px, 100%) 气泡宽；clamp(0px,(100%-480px)*.4,20%) 对侧缩进（human 最小 32px）
```

**注意**：Grok Bot **没有** ZCode 那种 `--ui-font-size` 用户密度旋钮——
它用固定字号 + 四档控件高度表达密度。AgentNodes 定位是"对话阅读型"，
建议保留密度旋钮（§10.5.3），但**先落刻度再谈旋钮**。

### 11.9 三层命名空间可从前缀反推（结构启示）

| 前缀 | 层 | CSS class 数 | 引用数 |
|---|---|---|---|
| `--cursor-*` | 平台层（Cursor/VS Code 血统，有 `--vscode-*` fallback） | `ui-*` 5,882 | 465 |
| `--sand-*` | 应用层（Grok Bot 自有） | `sand-*` 4,456 | 225 |
| `--ui-*` | 组件层（单组件内部变量） | — | — |

这比 ZCode「四层结构」更硬：不是文档分层，是**两套 StyleX class 家族 + 两套前缀**，
可从 token 名反推归属。AgentNodes 的 `canvas-*`/`wf-*`/`dag-*` 已是同方向，
缺的是"平台层 vs 应用层"的显式区分。

### 11.10 明确不抄

| 项 | 原因 |
|---|---|
| 云端电脑 / 浏览器 / 终端面板 | 产品形态不同：Grok Bot 托管执行环境，AgentNodes 是本地决策层 |
| **无 rewind/fork/checkpoint/trace/RSI** | Grok Bot 把工作放云端持续进行，不做会话回放。**U-D 仍只能以 ZCode 为模板** |
| StyleX / Base UI / TipTap / dnd-kit | 样式与组件方案已定，换库收益不抵成本（详见 GB 调研 §7） |
| 10 色族全量 | 那是 Bot 颜色标识需求；AgentNodes 只需轨迹五色 + 状态组 + viz 8 |
| 群聊 / Bot 间交接 / Slack / 语音 | 多 agent 协作与语音不在本轮范围 |

### 11.11 修订点累计（9 → 13）

接续 §10.7，U-A v2 修订点再增 4 个：

- ⑩ **双 alpha 表机制**：L0 除色值外增加 alpha 语义表（light/dark 各一套，同键不同值）
- ⑪ **specular 高光族**：`--spec-1..4`，与 shadow 分工（dark 用 spec / light 用 shadow）
- ⑫ **状态色 10 token 组**：StatusBadge 之前先定义组，否则 hover/pressed 无值可取
- ⑬ **刻度表前置**：间距/圆角/字号/行高/控件高五张表落进 A 批，B 批才有改写依据

> **待确认**：§11 全部为实证建议，未改变 §6 待决项的排序。6.1（转向确认）
> 仍是总前置。新增一条待决见 §6.10。

## §12 U-A 示意与现有前端复用评估（2026-11-22）

用户希望以 [U-A 三视图示意](../../tmp/mockup/agentnodes-ui-mockup.html) 的设计语言
实现前端，并标记不可直接复用的部分。可以采用中性主操作、蓝色品牌标记、语义状态色和
双主题；示意是静态样稿，不能把其中的数据、交互和完成状态当作真实能力。示意采用墨色
透明气泡，§2/§11.2 又提出浅色实心气泡：实施时以示意为视觉参考，同时验证双主题对比度，
不把两者视为已一致。现有生产 UI 尚未整体切换到该设计语言。

| 标记 | 对象 | 复用判断及缺口 |
|---|---|---|
| 复用逻辑，改样式 | [会话工作台](../../webui/frontend/src/components/layout/AppShell.tsx)、[会话侧栏](../../webui/frontend/src/components/chat/SessionSidebar.tsx)、[输入框](../../webui/frontend/src/components/chat/Composer.tsx)、消息及流式组件 | 保留会话管理、SSE、输入、工具调用和现有测试；按示意调整布局、密度、交互状态和响应式，不以静态 HTML 替换业务实现。 |
| 复用基础，重做令牌 | [主题状态](../../webui/frontend/src/stores/theme.ts)、[全局样式](../../webui/frontend/src/styles/globals.css)、[Tailwind 配置](../../webui/frontend/tailwind.config.js) | 保留主题切换和语义变量机制；当前仍有紫橙、渐变、辉光、硬编码颜色和缺失字体资源，不能直接视为 U-A。需逐页面验证文本对比、焦点和暗/亮主题。 |
| 复用部分逻辑，重做表面 | [审批对话框](../../webui/frontend/src/components/chat/PermissionRequestDialog.tsx)和 [AppShell 桥接](../../webui/frontend/src/components/layout/AppShell.tsx) | 已有四档按钮及提交接口；拒绝原因和失败回执已接入并有回归测试，仍需真实服务验证。示意中的侧栏审批队列和影响说明不能直接照搬。 |
| 复用部分，需接线验证 | [TraceViewer](../../webui/frontend/src/components/chat/TraceViewer.tsx) | 已有事件列表、筛选和详情；认证请求已改用统一客户端并添加回归测试，仍需真实服务验收。示意的完整 span 指标、注入时序、checkpoint 回退尚不能按样稿假定可用。 |
| 复用部分，需接线验证 | [上下文使用条](../../webui/frontend/src/components/chat/ContextUsageBar.tsx)、[任务面板](../../webui/frontend/src/components/chat/TodoPanel.tsx)、Agent 组件 | 可复用已有状态和展示逻辑；示意中的分段占比、进度及子代理详情须确认实际数据来源，不生成演示数值。 |
| 不可直接复用 | RSI 进化视图 | 示意中的信号源、评分、轮次和证据流为样例；虽有 [evolution 应用代码](../../src/agentnodes/apps/evolution)，当前 API 未挂载相应视图接口。需定义可用数据契约；缺失处标记「待接入」，不展示假数据。 |
| 保留功能，单独适配 | 登录、管理、[工作流](../../webui/frontend/src/pages/DAGPage.tsx)、[监控](../../webui/frontend/src/pages/MonitorPage.tsx)、Study、Run、因子及策略页面 | 不属于示意三视图的页面仍需统一外观并保留已有入口。Study/Run 后端路由已移除，其相关功能标记「后端不可用」，不因换肤假装已恢复。 |
| 不可直接复用 | 示意中的回退、分叉、完整队列编辑/撤回及通用生成式画布 | 仅将其作为设计目标；对尚未核实具备完整后端契约的操作标记「待接入」，不伪造可执行按钮。 |

建议按「双主题和响应式基线 → 语义令牌 → 会话工作台 → 审批与 Trace 接线 →
其他页面统一 → RSI 待接入」推进。每批保留原业务能力，验证真实数据、空态/错误态、
键盘操作和暗/亮主题；窄屏不能沿用示意图的固定三栏宽度。
