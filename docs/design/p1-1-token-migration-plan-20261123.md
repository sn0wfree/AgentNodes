# P1-1 令牌与基础组件迁移方案（U-A 设计语言落地）

> 日期：2026-11-23
> 性质：**方案（R-014 精神：方案先行，批准后才动代码）**；本文不改变任何代码。
> 依据：[U-A 令牌体系](./ui-a-token-system-design-20261114.md) A/B 两批、
> [UI 工作台设计语言](./ui-workbench-design-20261115.md)、
> [U-A 复用评估](./ui-ua-reuse-assessment-20261122.md)、
> [执行计划](./ui-ua-frontend-execution-plan-20261122.md) §6 P1-1、§3.2、
> [侧边栏业界调研](./ux-side-panel-industry-survey-20261123.md)、
> 示意 `tmp/mockup/agentnodes-ui-mockup.html`。

## 1. 为什么需要独立一批（问题陈述）

现网与 U-A 视觉的实测差距（2026-11-23 盘点）：

| 面 | 示意（设计目标） | 现网 | 差距 |
|---|---|---|---|
| 令牌体系 | 30 个语义令牌（中性灰阶 + 单强调蓝 + 三级文本） | 58 个变量，含 violet ramp / gradient-aurora / glass / dag-card-* / canvas-* | **交集仅 4 个**（border/primary/success/warning） |
| 消费通道 | 语义令牌直用 | slate-remap shim（~70 行 color-mix）+ Visual upgrade layer（~40 行）间接着色 | tailwind 类名为主通道，var() 直用少 |
| 情绪层 | 无渐变/辉光/玻璃 | 渐变令牌×5 + shadow-glow + .glow + 玻璃三类 + aurora 四层 | 设计文档要求全清 |

结论：现网的视觉接近是**零散局部改动**，不是体系复现；P1-1 是一次设计语言转向，
按设计文档细化为 **A-0 基线 → A 骨架（零视觉）→ B 转向（唯一价值观）→ B-2 amber → C 卫生** 五批（附 C）。

## 2. 设计输入与未裁决冲突

| 冲突 | 示意/工作台原文 | 处置（沿用计划 §0.5 裁决） |
|---|---|---|
| 浅色用户气泡 | 示意：墨色透明表面（`--bubble: rgba(23,23,23,0.05)`）；工作台 §11.2：浅色实心 | **以示意为基础**，以可访问性（正文对比度 ≥4.5:1）实机裁决；不宣称两份文档一致 |
| 单强调蓝 vs DAG 语义色 | 令牌方案：其余 ~49 处收敛为单强调蓝；DAG 节点状态色是语义例外 | 保留 DAG 语义色豁免（globals.css 中 dag-card-* 收编为 L3，不进 A 批清死变量） |
| 框架升级 | 设计文档：不把升级 Tailwind/shim 作为完成 U-A 的前提 | 不升级框架、不顺手替换依赖；兼容成本评估另出验证 |

## 3. 令牌映射（示意 30 → 现网语义层）

> 详细值表见 §附 A（agent 事实回填后定稿）。映射三原则：
> 1. 语义令牌 = 示意名字与值；L1 层唯一真源，L2 值指向 L1（color-mix 允许）。
> 2. 现网 dag-card-* / canvas-* 收编 L3，保留语义例外，不进 A 批删除清单。
> 3. 死变量 15 个在 A 批清除；渐变/辉光/玻璃在 B 批删除，组件消费改走语义层。

（待事实回填：30 个令牌逐项映射表 / 死变量清单 / shim 消费清单）

## 4. 批次与步骤

### A 骨架批（零视觉变化）

**选择器级联约束**：现网 dark 定义于 `:root, [data-theme='dark']`（L6–49）、light 定义于 `[data-theme='light']`（L52–90），两者特异性相同（0,1,0），light 靠**定义顺序在后**覆盖 dark。A 批重排必须保持「dark 在前、light 在后」，否则基线主题注入（localStorage sr-theme + data-theme）失效，pixel diff 门必红。

**硬约束（2026-11-23 实测）**：tailwind.config `borderRadius.xl=0.875rem(14px)` / `2xl=1.125rem(18px)`，
与刻度表（xl=12 / 2xl=14）**不一致**——A 批只允许**新增**刻度 CSS 变量声明，
**禁止改 tailwind 值或任何现有消费点**（值改写属 B 批，改动即破 pixel diff=0）。
同理 `--radius: 0.625rem` 保留定义不动（即使零引用，等 A 批死变量清单裁决）。

1. globals.css 按 L1/L2/L3 重排：L1 落示意语义值（名字+两主题值）；L2 值改指向 L1，视觉不变。
2. 清除死变量 15 个（清单待回填）；dag/canvas 令牌收编 L3。
3. 刻度表（§2.5 五张表）落地；alpha 表骨架落地。
4. 验证：主题切换、既有偏好、全部页面截图 **与改前逐像素一致**（零视觉回归）；组件测试 + tsc/lint 全绿。

### B 转向批（唯一价值观）

1. tailwind.config：violet ramp → blue ramp，accent ramp 收敛为单强调。
2. L1 值层落中性灰阶 + 单强调蓝（值变化集中在此批）。
3. 删除渐变令牌×5 / shadow-glow / .glow / 玻璃三类 / aurora 四层；
   组件里的渐变 CTA / 辉光阴影 / 玻璃表面改走语义表面 + 细边框。
4. slate-remap shim 消费清单逐项迁移到语义令牌；Visual upgrade layer 按清理结果收缩或删除。
5. 逐页面视觉对照（共享控件 → PageShell → 独立页面），不以全局机械替换覆盖图表/状态/DAG 语义色。

## 5. 组件清单与对照矩阵

- 共享控件：按钮、输入、下拉、弹窗、菜单、提示、标签、列表、空态、加载、错误态
- 页面：Login / AppShell(Chat) / Monitor / DAG / Study / Run / 设置 / 管理
- 矩阵：每个控件 × 深/浅 × 320/768/1440；键盘 focus-visible + 200% 缩放 + reduced-motion
- 基线：改前截图留档（固定路由/视口/主题/数据状态），改后逐项对照；差异须登记原因

## 6. 不做

- 不改路由、不改功能、不改后端、不改 SSE 契约
- 不升级框架/依赖；不引入新组件库
- 不碰并行会话在途文件；不做全局机械色替换伤害 DAG/图表语义
- 不在本批恢复 Study/因子/策略导航入口（P10 入口诚实性，图标已精简 6c27a72e）

## 7. 验证与门禁

- 每批：受影响测试 → 全量前端（`npm test`，现 124 文件/1353 测试基线）→ tsc → lint（0 错误，既有警告单列）
- 视觉：§5 矩阵截图 + 对比度实测（正文 ≥4.5:1、大号文本 ≥3:1、控件边界/焦点 ≥3:1）
- 若触碰 src/ 或 tests/ 之外无后端影响：不走 pytest/batch_gate；若改到后端门禁用 R-007 三件套
- 每批路径限定提交（不 push）；§12 执行记录回填真实日期/命令/通过数/证据

## 8. 待批准的决策点

**总前置（B.3）**：是否确认执行 **中性化转向**（violet→blue、中性灰阶、清情绪层）？
若选「保留紫橙 identity 只做骨架收敛」，U-A 退化为纯技术清理，B 批价值观不成立——
必须先答此项，否则不应开工（设计文档 §9.1 明列为总前置）。

其余 11 项内部冲突（浅色气泡/玻璃/双主题/aurora/零 diff 口径/A 批工序/文本层级/amber/状态色 token 数/批次节奏/字体）
及我的建议见 **附 B**，请逐条裁决或整体采纳建议。

硬纪律沿用：A-0 先补双主题基线；A 批承诺「基线覆盖面内 pixel diff = 0」+ 巡检；
每批独立提交（不 push）；§12 回填真实证据。

## 附 A：令牌映射事实表（2026-11-23 实测）

### A.1 示意 29 令牌 → 现网落点

示意每主题 29 个变量（light 9–31 行 / dark 34–56 行），分四族：

| 族 | 示意变量 | 现网对应 | 处置 |
|---|---|---|---|
| 表面/边框 | `--bg` `--surface` `--surface-2` `--border` `--border-strong` `--hover` `--selected` | 现网 `--background/#0a0a0f`、`--card`、`--muted`、`--border` 语义相近但**值不同** | L1 用示意名与值；slate-remap 值重指向（机制保留） |
| 文本三级 | `--fg` `--fg-subtle` `--fg-subtlest` | 现网 `--foreground/#e8e8ed`、`--muted-foreground/#8b8b96` | 示意无 `--fg-quaternary`（工作台 §11.5 要求四级——未裁，见附 B） |
| 主操作/品牌 | `--primary` light `#171717` / dark `#ffffff`（反转）；`--brand` sky-500/400 | 现网 `--primary` 是 **violet #8b5cf6**（浅色 `#ea580c` 橙！）；tailwind primary/accent 两 ramp 全 violet | **B 转向批核心价值观**：tailwind violet→blue ramp、L1 落中性+单强调蓝 |
| 状态色 | `--success/-fg/-surface`、`--warning/*`、`--danger/*`（示意 danger=现网 destructive） | 现网 `--success/#10b981`、`--warning/#f59e0b`、`--destructive/#ef4444` | 改走语义令牌；amber 装饰 144 处按 B-2 机械映射（不再当决策点） |
| 气泡 | `--bubble` light 墨 5% / dark 白 6% | 现网无独立 bubble（旧 `--gradient-user-bubble` 要删） | 按示意落墨 alpha，**以可访问性实机裁决**（附 B.1） |

### A.2 现网 globals.css 结构（652 行）

- L1 语义基础：8–45（dark）/ 53–86（light），26 个成对变量
- L2 旧情绪层：34–41 / 79–86，8 个（gradient-primary/aurora/glass/user-bubble/input-ring、shadow-soft/elevated/glow）
- L3a workflow canvas：518–524 / 528–534（7 个）；L3b DAG node card：607–628 / 631–651（13 个）
- slate-remap shim：**330–402（73 行，46 行含 color-mix）**；Visual upgrade layer：**404–437（34 行）**
- tailwind.config（43 行）：primary ramp violet-50..900（L7–18）、accent ramp purple-50..900（L19–30），**无 blue ramp、无单强调约束**

### A.3 消费通道统计（176 非测试源文件）

- Tailwind 类名赋值 **2056 次** vs 内联 `var()` **22 次（仅 7 文件）** → 类名通道约 **93:1**
- `var()` 直用只在无法用类名的场景：图表内联样式（ChartBlock/RunDetailPage）、workflow SVG/Canvas（WorkflowDAG/WorkflowEditor/DAGEdge）、DAGNode 兜底
- shim 消费面 = 几乎所有组件；因此 B 批值层改写可自动覆盖绝大部分 UI，情绪层组件改写约 **25 文件**（glass 12 处 / aurora 5 处 / glow 5 处 / shimmer 2 处 / gradient tailwind 3 处 / shadow-* 多处）

### A.4 删除/改造清单（出处带行号）

| 旧物 | 清单 | B 批处置 | 出处 |
|---|---|---|---|
| 渐变×5 | primary / aurora / glass / user-bubble / input-ring conic | 删除；**但全仓 `gradient` 232 处中 140 mask + 19 垫片 + 41 发丝 + 7 点阵 + 8 color-mix 是合法用法**，真装饰渐变仅 1 处且死代码——hygiene 门按「多色 且 非 mask 且 非单色垫片」三条件判定，禁止一刀切 | U-A 方案 29/60–63 |
| 辉光/微光 | `shadow-glow` `.glow` `.glow-border` `.shimmer` | 删除；消费组件改语义边框/普通阴影 + specular 高光（specular 族 A 批立占位值不动） | U-A 方案 128/154–155 |
| aurora 四层 | grid / aurora / vignette / grain | 删除；背景回归纯色 + 细网格可选 | U-A 方案 29/152–153 |
| glass 三类 | `.glass` / `.glass-strong` / `.glass-elevated` | **不删机制**——降为可关层（`data-*` 开关）；U-A 方案原文「玻璃本身不是罪，无条件玻璃才是」 | U-A 方案 128/156/194–197 |
| 死变量 15 | `--radius` `--ring` `--secondary` `--destructive` 等 | A 批清除 | U-A 方案 27/127/157 |
| light 翻橙 | `#ea580c` | B 批消失（两主题共用同一强调色） | U-A 方案 28/150 |

## 附 B：设计文档内部未裁决策（实施前需你拍板，别默认已有共识）

| # | 冲突 | 两方原文 | 我的建议 |
|---|---|---|---|
| B.1 | 浅色用户气泡 | 示意墨 5% 透明（示意 21/671）；工作台 §11.2 改 light 实心 100% / dark 通透 18%（主文档 345）；§12 明说「不视为一致」（481–485） | 按执行计划 §0.5：示意为基础，双主题对比度实测裁决 |
| B.2 | glass 去留 | 彻底删（工作台 §11.1 旧精髓）vs 降可关层（U-A 194–197 推荐） | 采纳可关层，避免大范围改 glass 消费点 |
| B.3 | **总前置：转向确认** | 中性化转向（推荐）vs 保留紫橙 identity 只做骨架收敛（U-A 退化为技术清理） | **必须先答**——不确认就不该开 B 批 |
| B.4 | 双主题去留 | 保留 dark+light 都中性化（推荐）vs 只留 dark | 保留双主题（工作台 §9.2 强制保留手动切换机制） |
| B.5 | aurora 替代 | 纯色+细网格（推荐）vs 完全纯色 | 纯色+细网格可选 |
| B.6 | A 批零 diff 口径 | U-A「pixel diff=0（50 基线）」vs 工作台「承诺应为基线覆盖面内零 diff + 5 个高频页人工巡检」；基线数量 50 vs 47 vs 84（双主题 A-0） | 采纳工作台口径：A-0 先补双主题基线（~84），承诺「覆盖面内零 diff」+ 巡检清单 |
| B.7 | A 批工序 | U-A v1：shim B 批仅值重指向；工作台 §11：v4 @theme 迁移 + shim/postcss/autoprefixer 退役进 A 批 | 需先核实现网构建链（工作台称 @tailwindcss/vite 是死依赖、现网纯 v3）——不升级框架（P1-1 硬约束），倾向 U-A v1 口径 |
| B.8 | 文本层级 | 示意三级 vs 工作台 §11.5 四级（+quaternary） | 按示意三级（示意是视觉目标），四级若需要另批 |
| B.9 | amber 处置 | U-A 仍列决策点 vs 工作台实测后「降为机械映射、待形式确认」 | 形式确认「机械映射」，按 --warning 10 token 组执行 |
| B.10 | 状态色组 token 数 | 文档写「每组 10 token」但实际列举 9 项 | 按实际列举的 9 项实现，文档措辞在 C 批修 |
| B.11 | 批次节奏 | A→B→B-2→C 连续 vs 一批一确认 | 一批一确认（符合本会话纪律） |
| B.12 | 字体 | 保留 Geist vs 换系统栈 | 按 P1-1「系统字体优先，避免依赖未交付字体文件」换系统栈 |

## 批准记录（2026-11-23）

用户已**采纳全部建议**：确认执行中性化转向（B.3），附 B 其余 11 项按建议裁决——
浅色气泡示意为基础+可访问性裁决 / glass 降可关层 / 保留双主题 / 纯色+细网格 /
A 批「覆盖面内零 diff」+ A-0 双主题基线 / A 批不做 Tailwind v4 迁移与 shim 退役 /
文本三级 / amber 机械映射 / 状态色按实际 9 token / 一批一确认 / 系统字体栈。
授权从 A-0 开始逐批推进；每批独立提交（不 push），后批开工前仍需单独确认。

## 附 G：B-2 amber 机械映射批前置事实（2026-11-23）

B 批只落地了 **3-token 状态组**（`--warning` / `-fg` / `-surface`，dark `#facc15` / light `#ca8a04`，globals.css L28/L55），U-A §2.3 的 **9-token 组尚缺 6 项**：`{role}Fill / FillHover / FillPressed / FillBright / ForegroundHover / ForegroundBright / Quaternary`。B-2 必须先补全 9-token 组（dark/light 各 9 项），再做 amber 类名映射——否则「机械映射到 --warning token 组」无处可指。

映射纪律（工作台 §10.3）：144 命中实测全为 warning 语义 → 降为机械映射，不再逐处决策；装饰性 amber 若 agent 分类有不确定项，单独列出待裁决，不默认灰阶化。

### 分类实测与用户裁决（2026-11-23）

agent 逐处判定当前树 **96 行 / 49 文件**（首轮快照 92/45，与用户口径吻合）：warning 语义 **74**（69 源码+5 测试）、装饰性 **19**（17 源码+2 测试）、不确定 **3**。工作台 §10.3「144 全 warning、装饰≈0」的断言**不成立**（至少 17 处明确非状态用法：星标、搜索 mark、事件图例、导航/文件夹图标、agent 身份色、CTA 按钮）。

用户四项裁决（全采纳推荐）：
1. **接受分类结论**：放弃「144 全 warning」前提；74 处 warning→9-token 组机械映射，19 处装饰剥离（灰阶/品牌蓝或 C 批 viz 收编）。
2. `TraceViewer.tsx:76` tool_result 事件色 = **装饰**（事件类型分类色），交 C 批 viz 收编。
3. `WorkflowEditor.tsx:558` 审批节点说明文字 = **warning**，映射 `--warning-fg`。
4. `nodeTypes.ts:44` approval 节点 `#fbbf24` = **保持 DAG L3 语义豁免**，不进 B-2。

执行面：74 处 warning 语义按 §D.2 替换表机械映射到 9-token 组；19 处装饰按文件分组改灰阶/品牌蓝；tailwind.config 增 `warning` 色入口指向 CSS 变量；7 处测试断言同步；`badge-awaiting` 两主题行同时替换；最后按附 E 重录受影响基线。

## 附 F：B 转向批可执行清单（2026-11-23 两路调研回填）

### F.1 双轨 primary 语义（执行取舍，不扩权）

- **CSS `--primary`** = 中性反转：dark `#8b5cf6`→`#ffffff`、light `#ea580c`→`#171717`（示意主操作）；`--ua-action` 轨道已是示意值，保持。
- **Tailwind `primary-*` 类名** = 品牌蓝：violet ramp(50-900) → sky ramp（500=`#0ea5e9` / 400=`#38bdf8` 对齐示意 brand）。
- 同名不同层是现有架构（shim 只映射 CSS 变量、tailwind 类名独立），「把 primary-* 改名 brand-* 求单一真源」**超出已批准范围，本批不做**。

### F.2 值层先行（第一步，先改值构建，观察自动传导）

1. `tailwind.config.js`：primary ramp → sky-50..900（`f0f9ff/e0f2fe/bae6fd/7dd3fc/38bdf8/0ea5e9/0284c7/0369a1/075985/0c4a6e`）；accent ramp → blue-50..900（`eff6ff/dbeafe/bfdbfe/93c5fd/60a5fa/3b82f6/2563eb/1d4ed8/1e40af/1e3a8a`）。
2. `globals.css` L1 值层：dark `--background #262626` / `--foreground #e5e5e5` / `--card rgba(255,255,255,.045)` / `--muted rgba(255,255,255,.07)` / `--muted-foreground #a3a3a3` / `--accent #38bdf8` / `--border rgba(255,255,255,.11)` / `--sidebar #1b1b1b`；light 对应 `#fafafa/#404040/#fff/rgba(23,23,23,.04)/#737373/#0ea5e9/rgba(23,23,23,.1)/#e9e9e7`。
3. 重新声明 A 批删掉的语义状态色 9 令牌（附 B.10）：dark `--success #4ade80`/`--warning #facc15`/`--danger #f87171`（+`-fg`/`-surface`）；light `#16a34a`/`#ca8a04`/`#dc2626`（+对应 fg/surface）。
4. alpha 表按 U-A §2.2 改值（附 B.1「示意为基础+可访问性裁决」）：bubble-user dark 18%/light 100% 但须双主题对比度实测；shadow/specular/scrim 互补对按 §2.2。

### F.3 情绪层拆除（第二步）

**删定义**：`--gradient-primary` `--gradient-aurora` `--gradient-input-ring` `--shadow-glow` 及 `.glow` `.hover-shadow-glow` `.glow-border` `.shimmer` `.aurora-backdrop` `.aurora-layer`（零消费类直接删，有消费类先改组件）。
**Visual upgrade layer**（A 批后 L428–461）：4 条 `--gradient-primary` 改纯色（指向 `--accent` 品牌蓝或 `--ua-action`）、删 `--shadow-glow` 与 brightness filter。
**glass 降可关层**：`.glass`（9 处）/ `.glass-elevated`（5 处）保留机制，加 `data-fx="off"` 时降级为纯 `--surface`；`.glass-strong` 零消费直接删。
**shim 本体**（L354–426）对情绪层变量零引用 → 保持不动。

### F.4 组件改写（第三步，~15 文件）

- `shadow-glow` 3 处：AssistantMessage L435/446（streaming dot→纯色脉冲）、StudyCreateForm L323（CTA→specular 细边框）
- `glow-border` 2 处：SessionSidebar L164 / FactorLibraryPage L52 → `:focus-visible` 统一蓝环（已全局兜底，删类名即可）
- `shimmer` 3 页：MonitorPage L72/240/290 / StrategyLibraryPage L129/130 → 加载态用 Skeleton 现有组件替代
- `aurora` 3 文件：RegisterPage L36/38 / PageShell L33/35 / DAGPage L27/29 → 删挂载层，背景回归纯色（细网格可选）
- 装饰渐变 2 处：StudyCreateForm L323 / StudyTaskSummary L214 → 纯色品牌蓝
- violet 直用类名 15 行（ThinkingBlock ×9 / TraceViewer L78-79 / ComposerStatusBar L23 / JsonActionCard L42 / TopBar L128 / agentStyles L40 / MonitorPage L217）→ 随 ramp 自动变蓝，无需逐个改（tailwind 类值已变）

**dag-card selected shadow**（L641/664）引用 `--primary`（将变中性）→ L3 语义例外：保留机制，选中态若要蓝需重指向 `--accent`（本批按「值随 L1 自动变中性」执行，不额外造蓝）。

### F.5 基线重录

按附 E：tag `p1-1-baselines-v1` 已锚定；代码提交与基线提交分离；5 页巡检；5 张 DAG 孤儿基线标注不在 88 内。

## 附 E：B 转向批基线重录程序（2026-11-23 调研定稿）

**前置锚点已落地**：lightweight tag `p1-1-baselines-v1` → `b1fee35b`（B 批前 88 基线永久可审计）。

**重录机制事实**：`save_baseline()` 用 `actual.replace(baseline)` 原地覆盖、无备份；唯一触发是环境变量 `UPDATE_SNAPSHOTS=1`（源码 docstring 里的 `--update-snapshots` 是假选项，未注册）；必须同时带 `AN_RUN_BROWSER_TESTS=1`，且**两个测试文件都要显式指定**（install.sh / e2e.yml 只跑 visual，会漏 catalog）。基线缺失时是 skip 而非 fail——所以「重录全绿」不等于视觉正确，必须配人工对照。

**执行步骤**（代码改完后）：
1. 工作区对照副本：`copy tests/baselines\*.png tests\baselines_v1\`（不入 git，仅本地并排；全量审计靠 tag）
2. 重新构建前端（conftest 依赖 webui/static）：`cd webui/frontend && npm run build`
3. 重录：`set AN_RUN_BROWSER_TESTS=1 & set AGENTNODES_TEST_CHAT=1 & set UPDATE_SNAPSHOTS=1` →
   `python -m pytest tests/test_webui_visual.py tests/test_webui_catalog.py -v --tb=short`
4. 关闭重录（`UPDATE_SNAPSHOTS=0`）立刻自证对比：skip 数应为 0（除既有 empty 场景条件 skip）
5. 5 高频页人工巡检（1440×900 / DSF1 / zh-CN / reduced-motion，dark+light）：
   `/login`（login_page*）、`/chat`（message_bubble*/assistant_message*）、`/`（Monitor，无现役基线→tag 前手工截图）、`/dag`（节点态，注意 5 张 DAG 孤儿基线是 attic 遗留不在 88 内）、`/catalog/*`（catalog_badge-default / tool-call / dag-node-failed 等高信号 story）
6. **代码提交与基线提交分离**：先提交前端代码（CSS/tailwind/组件/文档），再单独 commit `git add tests/baselines/*.png`（不加 3 个 JSON anchor、不加 baselines_v1/）；对照说明写入本节。

**孤儿基线**：5 张 DAG PNG（dag_visualization / dag_all_pending / dag_mid_running / dag_has_failed / dag_all_completed）来自 attic 归档测试，无现役场景、无 light 版——B 批重录不会更新它们，在对照说明中标注「不在 88 有效基线内」，是否 `git rm` 另行裁决。

## 附 D：A 骨架批可执行清单（2026-11-23 实测回填）

### D.1 死变量 15 个（数量精确吻合，全部删除）

`--card-foreground` `--popover-foreground` `--primary-foreground` `--secondary`
`--secondary-foreground` `--accent-foreground` `--destructive` `--ring`
`--sidebar-foreground` `--sidebar-border` `--success` `--warning`
`--gradient-user-bubble` `--ease-expo`（仅 dark）`--radius`（仅 dark）
— 各在 dark(L11/13/15/19/20/24/25/28/30/31/32/33/37/44/45) 与 light(L56/58/60/64/65/69/70/73/75/76/77/78/82) 定义处移除。

**保留**：`--sidebar` `--ease-spring` 属「伪消费」（仅被零使用 shim 类引用），严格口径不算死变量，本批不动。
**关键**：`--success`/`--warning` 虽零 `var()` 引用，但 tailwind `text-success-*` 类在组件中消费——不动 tailwind 配置，只删 CSS 变量声明，像素不变。

### D.2 新增 L1 primitives 段（插在 light 块结束 L90 与 `html` L92 之间）

因 dark 定义在 `:root, [data-theme='dark']`、light 在 `[data-theme='light']`，新段按同结构各声明两套：

1. **alpha 表（先只填现值）**：`--alpha-shadow-soft`(dark 30% / light 8%)、`--alpha-shadow-elevated`(60%/14%)、`--alpha-bubble-user`(7%/7%)、`--alpha-border-subtle`(60%)、`--alpha-border-strong`(80%)、`--alpha-text-secondary`(92%)、`--alpha-text-tertiary`(78%)、`--alpha-scrim`(25%/6%)、`--alpha-grid-line`(6%)、`--alpha-glow`(15%)、`--alpha-shimmer`(12%)、`--alpha-selection`(30%)、`--alpha-specular-hint`(50%/35%) —— 共 13 项，值取自 110 行 rgba/color-mix 字面量。
2. **specular 族占位（值不动）**：`--spec-1: #ffffff1f`(12%)、`--spec-2: #ffffff08`(3%)、`--spec-3: #ffffff05`(2%)、`--spec-4: #ffffff03`(1%)，两主题同值，只声明不引用。
3. **五张刻度表（只声明不改消费点）**：
   - 间距 `--space-0-25`…`--space-20`（40 档，1px→80px，4px 基数 + 0.25 细分，值照 U-A §2.5 L113）
   - 圆角 `--radius-none/xs/sm/base/lg/xl/2xl/3xl/4xl/full`（0/2/4/6/8/12/14/16/18/9999px）
   - 字号 `--font-size-xs/sm/base/lg/transcript`（11/12/13/14/15px）
   - 行高 `--line-height-xs/sm/base/lg/transcript`（14/16/18/22/24px）
   - 控件高 `--control-h-sm/md/lg/xl`（20/24/28/32px）

### D.3 零视觉变化保证

- 全部新变量**只声明无消费**；现有 110 行 rgba/color-mix、7 处 border-radius 字面值、9/10/11px 类名一个都不改
- tailwind.config `borderRadius.xl=14px/2xl=18px` 不动（与刻度表 xl=12/2xl=14 不一致属已知，B 批裁决）
- dark 在前 / light 在后的选择器顺序不变 → theme 注入有效
- 验收：双主题 e2e 88 基线 pixel diff=0 + 5 高频页巡检

## 附 C：修订后的批次结构（含 A-0）

| 批 | 性质 | 内容 | 验证门 |
|---|---|---|---|
| **A-0 基线补齐** | 零代码 | catalog/visual 测试双主题参数化，dark+light 各录一套（~84 基线） | 新基线全绿 = light 盲区关闭 |
| **A 骨架** | 零视觉 | globals.css L1/L2/L3 重排；L2 值指 L1；死变量 15 清除；dag/canvas 收编 L3；五张刻度表 + alpha 表骨架；specular 族立占位 | **基线覆盖面内 pixel diff = 0** + 5 高频页人工巡检 + vitest |
| **B 转向** | 唯一价值观 | tailwind violet→blue、L1 落中性+单强调蓝；删渐变/辉光/aurora；glass 降可关层；slate-remap 值重指向（机制保留）；情绪层 ~25 文件改写 | 基线重录（附对照说明）+ vitest + webui e2e |
| **B-2 amber** | 机械映射 | 144 处 amber → --warning token 组 + StatusBadge 组件化 | 同 B（避免 B 主批 diff 不可审） |
| **C 卫生** | 门禁 | viz 收编 ~60、散 hex ~49、9/10/11px→刻度、`test_token_hygiene.py` 门（三条件判定 + 注入即红） | hygiene 门 + 全绿 |

**说明**：原骨架 §4 的 A/B 两批已按设计文档证据细化为 A-0 + A + B + B-2 + C 五批；A-0 是前置（无基线无法验「零 diff」）。

### A-0 实测调查（2026-11-23）

| 项 | 实测 |
|---|---|
| 基线文件 | `tests/baselines/*.png` **47 个**（均为默认主题 dark，与工作台文档 47 口径一致；U-A 方案写的 50 是旧数） |
| 视觉测试 | `tests/test_webui_visual.py`（498 行）8 场景：login / message bubble / user bubble only / assistant markdown / command palette / empty chat / empty goal / empty agent |
| catalog 测试 | `tests/test_webui_catalog.py`（215 行）3 场景：catalog index / story baseline / story not found |
| 机制 | Playwright pixel diff（PIL ImageChops，阈值 10，差异 ≤0.5% 通过），`UPDATE_SNAPSHOTS=1` 重录基线 |
| 车道 | **opt-in**：需 `AN_RUN_BROWSER_TESTS=1` + playwright + 真前端服务（默认全量 pytest 里 skip，不污染 collect 锚点） |

**A-0 实施内容**：给 11 个场景×2 主题参数化（dark 沿用现有 47 基线名，light 新增 `*_light.png` 约 47 个 → 双主题基线约 94 张；设计文档预估 84，以实测 47×2=94 为准）；catalog 同步参数化；更新文档口径（50/47/84 → 实测 47→94）。
**验收门**：双主题基线全绿 = light 盲区关闭；浏览器 e2e 车道单独跑（不改默认全量 pytest 行为）。
**前置**：需要真实前端服务 + Playwright 可用；当前环境 vite 5173 在跑，浏览器通道可用——开跑前确认依赖。
