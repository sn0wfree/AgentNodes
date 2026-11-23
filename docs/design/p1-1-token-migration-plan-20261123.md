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

## 附 C：修订后的批次结构（含 A-0）

| 批 | 性质 | 内容 | 验证门 |
|---|---|---|---|
| **A-0 基线补齐** | 零代码 | catalog/visual 测试双主题参数化，dark+light 各录一套（~84 基线） | 新基线全绿 = light 盲区关闭 |
| **A 骨架** | 零视觉 | globals.css L1/L2/L3 重排；L2 值指 L1；死变量 15 清除；dag/canvas 收编 L3；五张刻度表 + alpha 表骨架；specular 族立占位 | **基线覆盖面内 pixel diff = 0** + 5 高频页人工巡检 + vitest |
| **B 转向** | 唯一价值观 | tailwind violet→blue、L1 落中性+单强调蓝；删渐变/辉光/aurora；glass 降可关层；slate-remap 值重指向（机制保留）；情绪层 ~25 文件改写 | 基线重录（附对照说明）+ vitest + webui e2e |
| **B-2 amber** | 机械映射 | 144 处 amber → --warning token 组 + StatusBadge 组件化 | 同 B（避免 B 主批 diff 不可审） |
| **C 卫生** | 门禁 | viz 收编 ~60、散 hex ~49、9/10/11px→刻度、`test_token_hygiene.py` 门（三条件判定 + 注入即红） | hygiene 门 + 全绿 |

**说明**：原骨架 §4 的 A/B 两批已按设计文档证据细化为 A-0 + A + B + B-2 + C 五批；A-0 是前置（无基线无法验「零 diff」）。
