# U-A 令牌体系：中性化转向 + 三层令牌架构（方案，R-014）

> 2026-11-14 · 分支 `ui-workbench` · 上游：`docs/ui_final_turn_001.md` §8.2（中性令牌系统）
> · 前置侦察：tmp/probe/token_inventory.py + ua_blast_radius.py（全部实测，2026-11-14）

## §0 结论

1. **U-A 不是「收敛散落变量」，是一次设计语言转向。** 现状令牌体系 = 紫罗兰主色
   （dark #8b5cf6 / light 翻橙 #ea580c）+ 渐变×5 + 辉光阴影 + 玻璃拟态三类 +
   aurora 氛围层（网格/极光/暗角/噪点）——与调研 §8.2 目标（中性灰阶承载层级 +
   单一强调色 + 禁渐变/辉光/玻璃）**逐项正面对撞**。禁用清单本身就是现状的
   「Visual upgrade layer」注释块。转向是产品价值观决策（§9.1），不是技术清理。
2. **结构与价值观分离，两批交付**：骨架批（三层重排 + 死变量清除，像素零变化）
   → 转向批（唯一价值观 commit：落中性值 + 拆情绪层 + 基线重录）。revert 粒度
   = commit，转向可独立回退。
3. **组件零改动的机制已存在**：globals.css 的 slate-remap shim（~70 行
   color-mix 重映射）+ tailwind.config palette。转向批只动「值层」（palette +
   CSS 变量 + shim 指向），组件 className 大部分不动；只有直接消费情绪层的
   ~25 个文件需要改写。
4. **图表色是合法特例**：109 处硬编码色中 ~60 处在图表组件（需要可分辨色相），
   单列 viz 令牌层豁免「单强调」规则，其余 ~49 处随卫生批收敛。

## §1 现状真值表（全部实测，2026-11-14）

| # | 事实 | 证据 |
|---|---|---|
| 1 | globals.css 653 行；变量 106 处定义 / 55 去重；**15 个死变量**（--radius/--ring/--secondary/--destructive 等定义未消费） | token_inventory.py |
| 2 | 双主题：dark 默认（紫 #8b5cf6）+ light 暖纸（**主色翻橙 #ea580c**）；`stores/theme.ts` setTheme/toggle（用户可切功能） | globals.css:38-116 |
| 3 | 情绪层清单：渐变令牌×5（primary/aurora/glass/user-bubble/input-ring conic）+ shadow-glow + .glow + 玻璃三类（.glass/.glass-strong/.glass-elevated）+ aurora 四层（grid/aurora/vignette/grain）+ .glow-border conic focus ring + .shimmer | globals.css:63-67,164-313 |
| 4 | 组件消费令牌的主通道 = tailwind 类名，经两层 shim 间接着色：slate-remap（~70 行）+「Visual upgrade layer」（~40 行，渐变 CTA/辉光阴影）——**直用 var() 很少**（top --border 33 次） | globals.css:331-438 |
| 5 | tailwind.config：primary = violet ramp 50-900，accent = purple ramp；组件 `primary-500`×41 文件、`primary-600`×19 文件 | tailwind.config.js |
| 6 | 情绪层直接消费面：glass×10 文件 / aurora×5 / glow×7 / gradient×8 / shimmer×2 / violet-*×6 / orange-*×3 / **amber-*×41 文件**（最大单点） | ua_blast_radius.py |
| 7 | 硬编码色 109 处 / 20 文件；图表四件套占 ~60（ChartBlock 20/MetricsTrendChart 16/PanelRenderCard 15/EquityCurveCard 9）；高频值 = tailwind slate 系泄漏（#94a3b8×12/#334155×9/#64748b×8）+ #3b82f6 蓝×9（**事实上的候选强调色**）+ #10b981 绿×8 | token_inventory.py |
| 8 | 间距纪律缺失：tailwind 任意值 px 直方图 10px×163 / 11px×74 / 9px×48（9/10/11 三档并存，4px 刻度未贯彻）；style={{ }} 内联仅 25 处 | token_inventory.py |
| 9 | canvas/wf 令牌 7 个 + dag-card 令牌 13 个（均双主题×2 定义）；--dag-card-bg 仍是渐变 | globals.css:515-653 |
| 10 | 验证锚点：tests/baselines 50 PNG（visual 8 场景 + catalog story 基线 catalog_*.png）；测试 test_webui_visual（8）+ test_webui_catalog（3）；重录通道 `--record-snapshots` | tests/baselines/ |

## §2 目标三层架构

```
L1 原语层（primitives，唯一允许出现字面值的层）
  灰阶 ramp --gray-50..950（双主题各一套，承载全部层级）
  强调色   --accent-base（单强调，蓝）          ← 全产品唯一情绪色
  状态色   --success / --warning / --destructive（**每组 10 token**，见 §2.3）
  viz 色   --viz-1..8（图表系列色，豁免单强调规则，单点定义）
  alpha 表 ×2（light/dark 同键不同 alpha，见 §2.2）
  specular 高光族 --spec-1..4（见 §2.4）
L2 语义层（roles，保留现有命名契约 → 组件零改动）
  --background/--foreground(+subtle/subtlest/quaternary)/--card/--popover/
  --muted/--border/--input/--sidebar/--primary/--accent/--success/--warning/
  --destructive/...
  值一律指向 L1（color-mix 组合允许）；不再定义渐变/辉光令牌
L3 组件层（现 canvas×7 + dag×13 收编为 L2 派生，值落 L1）
```

**禁用清单（§8.2，落进 hygiene 门）**：**多色** CSS 渐变、辉光阴影、装饰性
backdrop-filter、多 CTA 强调、conic 装饰、氛围层。字体（Geist Variable
自托管 + JetBrains Mono）与双主题机制**保留**，不在转向范围。

> **判定必须三条件**（Grok Bot 实测，见 `ui-grok-bot-source-research-20261116.md` §0#4）：
> `gradient` 关键字出现 232 处，其中 140 处是 `mask-image` 遮罩、19 处是同色双停靠
> 垫片、~41 处是 1px 发丝线、7 处点阵、8 处 `color-mix` —— **真装饰渐变仅 1 处且是死代码**。
> hygiene 门按「**多色 且 非 mask 且 非单色垫片**」判定，一刀切会误伤 200+ 处合法用法。

### §2.2 alpha 语义表（新增，v2 修订点 ⑩）

L1 层除色值外，**再定义一张 alpha 语义表**：同一语义位在 light/dark 有不同
alpha，而不是不同 hex。Grok Bot 实测（53 键，两主题键集完全相同）：

| 语义位 | light | dark | 纪律 |
|---|---|---|---|
| `bubble-user` | **100** | **18** | 用户气泡 light 实心、dark 通透（**推翻"墨 5% 通用"**） |
| `shadow/control` | **12** | **0** | **dark 完全不用阴影分层** |
| `specular/primary` | **56** | **12** | light 用强高光、dark 用微光 |
| `scrim` | 50 | 69.7 | 遮罩在暗底更重 |

**规则**：禁止"一个 alpha 打两主题"。`shadow/*` 与 `specular/*` 是**互补对**
（dark 用 spec / light 用 shadow）。

### §2.3 状态色 = 10 token 组（新增，v2 修订点 ⑫）

状态色不是单色，是一组：

```
{role}                    裸色（图标/边框）
{role}Fill / FillHover / FillPressed / FillBright
{role}Foreground / ForegroundHover / ForegroundBright
{role}Quaternary          surface 档
```

**理由**：B-2 批实测 144 处 amber 全为 warning 语义，但各写各的
`bg-amber-500/10 + border-amber-500/30 + text-amber-400` 三件套。
若只做 StatusBadge 而不定义组，**hover/pressed/disabled 态无处取值**，
组件化会立刻缺档。

### §2.4 specular 高光族（新增，v2 修订点 ⑪）

```css
--spec-1: 12%   /* 顶边高光 / 玻璃边 / squircle 描边 */
--spec-2:  3%
--spec-3:  2%
--spec-4:  1%
```

**这是 glow 的正确替代物**：glow 是"发光"，specular 是"反光"——中性仪表板
要的是后者。light 主题方向相反（Grok Bot light `56/16/12/6`，即深色描边），
故 spec 也进 alpha 表。

### §2.5 刻度表（新增，v2 修订点 ⑬，A 批落地）

| 表 | 档位 |
|---|---|
| 间距（40 档） | `0-25=1 / 0-5=2 / 0-75=3 / 1=4 / 1-25=5 / 1-5=6 / 1-75=7 / 2=8 … 3-75=15 / 4=16 … 20=80` |
| 圆角（10 档） | `none 0 / xs 2 / sm 4 / base 6 / lg 8 / xl 12 / 2xl 14 / 3xl 16 / 4xl 18 / full 9999` |
| 字号（5 档） | `xs 11 / sm 12 / base 13 / lg 14 / transcript 15` |
| 行高（5 档） | `xs 14 / sm 16 / base 18 / lg 22 / transcript 24` |
| 控件高（4 档） | `sm 20 / md 24 / lg 28 / xl 32` |

**这直接回答 §1#8**（任意值 9/10/11px 三档并存）：不需要发明刻度，
采用 4px 基数 + 0.25 细分即可。刻度表进 **A 批**（零像素变化），
B 批才有改写依据。

## §3 阶段切分与工序（R-020）

| 批 | 性质 | 内容 | 视觉 | 验证 |
|---|---|---|---|---|
| **A 骨架** | 非删除 | globals.css 按 L1/L2/L3 重排；L2 值改指向 L1（值不变）；死变量 15 个清除；dag/canvas 收编为 L3；**刻度表落地（§2.5 五张表）**；**alpha 表骨架（§2.2 两套表，先只填现值）**；specular 族立占位（值不动）；**零像素变化** | 不变 | pixel diff = 0（50 基线全绿）+ vitest |
| **B 转向** | 唯一价值观 commit | tailwind.config：violet ramp → blue ramp、accent ramp 收敛；L1 落中性灰阶 + 单强调蓝；渐变令牌×5/shadow-glow/.glow/.glow-border/.shimmer/aurora 层**删除**；**玻璃降为可关层**（`data-*` 开关，不删机制）；slate-remap 值重指向（机制保留）；情绪层消费组件改写（§1#6 的 ~25 文件） | **全变** | 基线重录（--record-snapshots + catalog 重录），重录 commit 附对照说明；vitest + webui e2e lane |
| **B-2 amber** | 非删除 | 144 处机械映射到 **--warning 10 token 组**（§2.3）+ StatusBadge 组件化；hover/pressed/disabled 态一次补齐 | 同 B | 同上 |
| **C 卫生** | 非删除 | viz 令牌收编（~60 处图表色）；非图表散 hex ~49 处收敛；任意值 9/10/11px → §2.5 刻度；**test_token_hygiene.py 门**（**三条件判定**，§2 禁用清单注 + 白名单外裸 hex + 注入测试） | 微调入基线 | hygiene 门注入即红（R-019）+ 全绿 |

B 批内部工序：值层先行（palette + CSS 变量，自动覆盖 remap 面）→ 情绪层拆除 →
组件消费点改写（glass/aurora/glow/gradient/shimmer/violet 逐文件）。amber×41
单独立小批（B-2），避免 B 主批 diff 不可审。

## §4 验证表（R-019：每个网说明它变红时抓住了什么）

| 网 | 钉住什么 |
|---|---|
| A 批 pixel diff = 0（50 基线） | 骨架重排零视觉变化——重排夹带值漂移即红；**刻度表/alpha 表落地也在此网下**（只加表不改值） |
| B 批基线重录 + 对照说明 | 转向后每组件/每场景的新权威态；重录即人审点 |
| test_token_hygiene.py + 注入测试 | **多色渐变（非 mask/非垫片）**/辉光/装饰性 backdrop-filter/裸 hex 复活即红；**mask 与单色垫片不告警**（§2 禁用清单注的三条件） |
| alpha 表一致性检查（B 批新增） | light/dark 两张表键集不一致、或某语义位两主题同 alpha 且属 shadow/specular 族即红 |
| vitest 全量（~100 文件） | 类名改写不破坏组件行为/结构断言 |
| webui e2e lane（catalog+visual） | 每批收口；基线机制本身可用 |

## §5 行为变化（显式）

1. 全产品视觉从「紫罗兰/橙 + 渐变玻璃辉光」转向「中性灰阶 + 单强调蓝」；
   dark/light 双主题保留，light 失去「主色翻橙」特性（两主题共用同一强调色）。
   **新增**：两主题在 alpha 层仍不同（§2.2）——同一语义位 light/dark 的透明度不同。
2. aurora 氛围层（AppShell/PageShell/Login/Register/DAGPage 挂载）删除——背景
   回归纯色 + 细网格可选（决策点 §9.3）。
3. `.glow` / `.glow-border` / `.shimmer` 类名删除，消费组件改用语义层对应物
   （PanelShell/边框/普通阴影 + **specular 高光**，§2.4）。
   **`.glass` 三类不删，降为可关层**（`data-*` 开关 + 刻度化 blur，决策点 §9.6）。
4. 15 个死变量删除；--radius 现未消费，转向批按需要重立或随删。
   **新增**：§2.5 五张刻度表成为唯一取值来源，任意值 px 不再允许。
5. 图表色合法化：viz 系列色成为唯一图表取色来源。
6. **状态色结构变化**：从单色升级为 10 token 组（§2.3），StatusBadge
   hover/pressed/disabled 态由令牌驱动而非组件内联。

## §6 风险

| # | 风险 | 缓解 |
|---|---|---|
| 1 | amber-*×41 文件是最大单点，B 主批夹带会不可审 | 拆 B-2 独立小批；状态语义（warning）与装饰泛用分开处置（决策点 §9.2） |
| 2 | 基线重录 = 回归网一次性重置，夹带缺陷会被「合法化」 | 重录 commit 必须附前后对照说明（人审点）；hygiene 门同批补位 |
| 3 | color-mix/remap shim 对新灰阶的组合效果不可逐一目测 | catalog story 每组件独立基线兜底 + light/dark 双主题各录 |
| 4 | tailwind v3.4.19 与 @tailwindcss/vite ^4.3.3 混装疑点（构建行为不明） | 动手前先核查（§7 遗留账前置项），必要时只动 config 不动构建链 |
| 5 | 组件测试断言了旧类名/旧色值（如 MessageBubble.test 断言 gradient） | B 批逐文件改写时同步 vitest；全量兜底 |

## §7 回滚与遗留账

- 每批独立 commit；A/B/C 各自可 revert；B 重录 commit 单独成点（回退 B =
  revert 值层 commit + 重录回旧基线）。
- 遗留：① remap shim 长期拆除（组件 className 真迁移，U-E 后评估，机制本身
  不是债）；② tailwind v3/v4 混装核查；③ 调研全文关键标记已在档
  （ui_final_turn_001.md 含 Ratatui/矩阵/Macaron 章节，无重复入库动作）。

## §9 讨论评估要点（供决策）

1. **转向确认（前置，价值观）**：执行 §8.2 中性化（推荐，调研四条参考的落点）
   vs 保留紫橙 identity 只做骨架收敛（U-A 退化为纯技术清理，U-B/C/E 的视觉
   价值打折）。
2. **amber×41 的处置深度**：状态语义保留（warning 场景继续 amber 但走令牌）
   + 装饰性 amber 收敛灰阶（推荐）vs 全部灰阶化（状态色也中性，信息密度受损）。
3. **双主题去留**：保留 dark+light 都中性化（推荐，theme store 是既有功能，
   删除超 U-A 范围）vs 只留 dark。
4. **aurora 背景的替代物**：纯色 + 保留细网格层（推荐，Codex 式「almost
   entirely neutral」但保留一点空间感）vs 完全纯色。
5. **工序**：A → B → B-2 → C 连续做（推荐，每批一 commit + 每批验证收口）
   vs 一批一确认。
6. **玻璃去留（新增，Grok Bot 实证）**：玻璃降为**独立可关层**——保留 `.glass`
   机制但默认关、走 `data-*` 开关、blur 走刻度（推荐，Grok Bot `glass.scss/
   glass.stylex` 独立 @layer 实证）vs 彻底删除（原计划，但实测证明"玻璃本身
   不是罪，无条件玻璃才是"）。
7. **状态色组粒度（新增）**：定义 10 token 组（推荐，§2.3，hover/pressed 有值可取）
   vs 只做三件套组件化（B-2 会立刻缺档）。
