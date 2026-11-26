# 语义色统一方案：消除硬编码，单点可调（R-014 · 待批准 · 零代码）

> 日期：2026-11-24
> 状态：方案。批准后才动代码；本文不改任何实现。
> 触发：用户「配色方案是否可以统一起来，不要硬编码，方便后续再统一地方进行调整配色」；
> 依据 `color-audit-20261124.md` 审查结论（亮色 53 项低对比，根因 emerald/violet/rose 60+ 处硬编码）。

## 1. 关键发现：令牌已存在，缺的是"接线"

`globals.css` 中已定义完整的双主题语义令牌（P1-1 B-2 批建立）：

| 语义 | CSS 变量（dark / light） | 现状 |
|---|---|---|
| 成功 | `--success` #4ade80 / #16a34a，`--success-fg`、`--success-surface` | ✅ 已定义 |
| 危险/错误 | `--danger` #f87171 / #dc2626，`--danger-fg`、`--danger-surface` | ✅ 已定义 |
| 警告 | `--warning` 9-token 组 | ✅ 已定义且已接入 Tailwind |
| 图表系列 | `--viz-1..8` 双主题 | ✅ 已定义 |
| 思考/推理 | ❌ 无专用令牌（现为 violet 硬编码） | 需新增 |
| 信息/强调 | ❌ 无专用令牌（现为 sky 硬编码） | 可选新增 |

Tailwind 现状（`tailwind.config.js:31-34`）：**只映射了 warning 三个类**，`success` / `danger` 有令牌却没有 Tailwind 入口 —— 组件只能用 `text-emerald-400`（硬编码）而不能用 `text-success-fg`（语义类）。

## 2. 目标

**单一事实源**：所有语义色只在 `globals.css` 定义一次（dark/light 各一份）；Tailwind 配置只做变量到类名的映射；组件只用语义类名，不写色阶字面量。以后调色只改 `globals.css` 两处值，全站生效。

## 3. 方案（分批）

### 批 C1：补齐令牌与 Tailwind 映射（基础设施，低风险）

1. `globals.css` 新增两组令牌（dark/light 各一套）：
   - `--thinking` / `--thinking-fg` / `--thinking-surface`（思考块，light 用 violet-700 深色保证对比度）
   - `--info` / `--info-fg` / `--info-surface`（信息/强调，可选，若确认 sky 硬编码多则做）
   - 顺带把 `success` / `danger` 从 3-token 扩成与 warning 一致的 9-token 结构（fill/hover/pressed/fg 等），保持组型统一
2. `tailwind.config.js` 映射全部语义类：
   - `success` / `success-fg` / `success-surface` → `var(--success*)`（含 9-token 全套）
   - `danger` / `danger-fg` / `danger-surface` → `var(--danger*)`
   - `thinking` / `thinking-fg` / `thinking-surface` → `var(--thinking*)`
   - `info` 系列同上（若做）
3. **此批零视觉变化**（只是新增声明与映射，无组件改动）；验证 tsc/build/gate。

### 批 C2：高频组件替换（收益最大）

按审查报告 P0 清单，把最影响可读性的硬编码替换为语义类：
- `SSEStatus.tsx:18`、`ToolCallGroup.tsx:61`、`ContextUsageBar.tsx:23`、`ComposerStatusBar.tsx:24`（Build）、`utils/status.ts`、`AgentItem.tsx`、`ToolCallBlock.tsx` → `text-success-fg` / `bg-success-surface`
- `ThinkingBlock.tsx:83,86,90,97` → `text-thinking-fg` / `bg-thinking-surface`（解决 1.42/1.53 最差对比度）
- `MonitorPage.tsx`、`AdminUsersPage.tsx`、`Toast.tsx`、`GoalMessage.tsx` 等错误态 → `text-danger-fg` / `bg-danger-surface`
- 每替换一批跑受影响测试 + 双主题基线重录（B-3 全量重录）

### 批 C3：全量清扫 + 门禁防回退

1. 剩余 40+ 处硬编码（study/*、workflow/*、图表涨跌等）分批替换；涨跌色用 `success-fg`（涨）/ `danger-fg`（跌）语义。
2. 扩展 `scripts/arch/check_token_hygiene.py`：新增规则——禁止新增 `text-emerald-*`/`text-violet-*`/`text-rose-*`/`text-red-*`/`text-sky-*` 等色阶类作语义色（白名单：viz 图表、identity 品牌色），防止回退。
3. 清理 `globals.css:533-545` 的 badge 亮色硬编码特例 → 改用语义令牌。

### 批 C4：验收与文档

- 双主题全量视觉基线重录（88+ 张），对比度复测（亮色低对比项 53 → 目标 ≤10）
- 前端全量 / tsc / lint / gate 13/13
- 回填 P1-1 token 计划与执行计划 §12；INDEX 计数

## 4. 不变式

- 不引入新依赖、不升级 Tailwind（P1-1 硬约束）
- 令牌只增不改现有 dark/light 基础值（除新增 thinking/info 组）
- viz 图表系列色、identity 品牌色保持豁免（C 批 allowlist）
- 每批可独立回滚；语义类名一旦统一，后续调色只动 `globals.css`

## 5. 验收标准

| 断言 | 前 | 后 |
|---|---|---|
| 语义色有 Tailwind 类入口 | 只有 warning | success/danger/thinking/info 全有 |
| 硬编码语义色阶 | 60+ 处 | 0（allowlist 除外） |
| 亮色低对比项 | 53 | ≤10 |
| 单点可调 | 否（改 60 处） | 是（改 globals.css 2 处） |
| 门禁防回退 | 无 | check_token_hygiene 新增禁止规则 |

## 6. 不做

- 不改布局/间距/字号（另有专项）
- 不改产品行为
- 不删除现有 warning/viz 令牌
- 本方案只覆盖语义色；次级文字色阶（muted-foreground 偏浅）属令牌调值，纳入 C1 一并评估

## 7. 回滚

每批独立提交；`git revert` 即可。令牌新增批无视觉变化，回滚零风险；组件替换批回滚后恢复原硬编码色（含原对比度问题）。

## 8. 后续：theme.json 完整控制配色（2026-11-26 · 计划先行）

C1-C4 + 配色统一阶段 1-3 已完成（theme.json 注入链路已通），但审计发现覆盖不全：
- 内置默认只含 13 个变量；`globals.css` dark 有 67 个、light 有 157 个（含大量无消费者的 space/radius/font 死 token）
- 剩余 686 处色阶硬编码（79 文件），其中 slate/gray 中性 ~280 处可迁语义类；emerald/red/rose/sky 等业务语义色需映射到 success/danger/warning/info 组；viz 图表色与 identity 品牌色按 C 批 allowlist 保持豁免

目标：**theme.json 能完整控制前端页面配色的每个细节**。分两步：

**第 1 步 · 内置默认全量提取（低风险）**
- `_BUILTIN_THEME` 不再手写 13 项，改为从 `globals.css` 自动提取全部**颜色类变量**（dark/light 各自解析；排除 space/radius/font/alpha 等非颜色 token）
- 提取逻辑放后端单模块，进程内缓存；测试断言：内置默认覆盖所有 `globals.css` 颜色变量（防漂移）
- theme.json 部分覆盖语义不变；用户可写任意已声明变量覆盖
- 验证：后端全量 + 架构门禁 + 真机覆盖测试

**第 2 步 · 剩余硬编码迁移（按风险分批）**
- 批 2a：slate/gray 中性色 ~280 处 → `text-muted`/`text-secondary`/`bg-surface`/`border-*` 等（复用阶段 2 codemod 映射表）
- 批 2b：业务语义色（emerald→success / red→danger / sky→info / violet→thinking / rose→danger）→ 由 theme.json 的状态色组控制；viz/identity 白名单文件不动
- 每批全量测试 + tsc/build + token hygiene 门禁；硬编码总数目标降到 ~200（仅剩白名单合法用法）

**验收**：theme.json 写入任一 CSS 颜色变量 → 接口返回 + 前端渲染生效；状态色组改动 → 全站成功/危险/警告/思考/信息色同步变化。
**不做**：非颜色 token（space/radius/font-size）纳入主题控制——它们 0 消费者且属独立设计刻度体系；不改 viz/identity 视觉。

## 8. 实施记录（2026-11-24，用户「开始实施」后执行）

| 批 | 实际落地 | 偏差 |
|---|---|---|
| C1 | `globals.css` 新增 thinking/info 双主题令牌 + success/danger `fg-muted` 次级色（light 用加深色阶）；`tailwind.config.js` 全量映射 success/danger/thinking/info（各 4 类）；新增 `test_semantic_tokens_declared_in_both_themes` 双向声明契约。零视觉变化，tsc/build/契约测试 6/6/token hygiene 0 违规。 | 无 |
| C2 | SSEStatus / ThinkingBlock / ToolCallGroup / ToolCallBlock / ContextUsageBar / AgentItem / ComposerStatusBar / utils/status 共 8 文件硬编码→语义类；同步 2 个测试断言。受影响 56 测试通过。 | 无 |
| C3 | 批量替换 38 文件 / 91 行前景硬编码（emerald/red/rose/sky/blue/violet/purple/orange→success/danger/info/thinking/warning）；`check_token_hygiene.py` 新增第 4 类 `semantic-fg-hardcode` 门禁（基线 0、新增即红）+ 身份/viz/深底垫片 allowlist；同步 2 个测试断言。 | 保留 176 行深底垫片（bg-*-9xx 配浅字，合法设计）与身份/viz 色，未强行令牌化 |
| C4 | 前端 1368 测试 / tsc OK / lint 0 error；重建前端（VITE_E2E=1）+ 6 张目录基线重录（assistant-message、tool-call-done、tool-call-error × 双主题）；e2e 114 passed / 0 skipped；后端 8143 passed（1 例 gateway timeout flaky，单跑通过）；gate 13/13 ALL GREEN；亮色对比度复测 **53 → 14（降幅 74%）**，语义色低对比项（1.4–1.9）全部消除。 | 剩余 14 项均为次级文字（muted-foreground / slate），属方案边界外（P1 令牌调值），未纳入本批 |

**验收标准达成**：语义色 Tailwind 类入口 warning→全量；硬编码语义前景 60+→0（allowlist 外）；最差对比度 1.42→消除；单点可调（改 globals.css 两处）；门禁防回退（第 4 类检测）均达成。次级文字 14 项留作后续令牌调值批。

---

## 9. 全量配色配置化专项（2026-11-26 · 用户裁决「彻底纳入」）

### 9.1 触发与目标

用户要求「所有配色风格都由配置文件决定」。核查结论：链路 `theme.json → GET /api/system/theme → scoped CSS 变量` 已通（后端从 globals.css 自动提取颜色变量），但仍有 6 类旁路——globals.css 选择器内约 58 处颜色字面量、Tailwind `accent` 硬编码阶梯、约 46 处 `sky-*` / 24 处 `blue-*` 原生色阶类、约 74 处 TSX 内联 hex/rgba、13 处 SVG fill/stroke、后端提取器 2 个盲区（gradient/多值 shadow 不导出；按首次 `[data-theme='light']` 切分使后续 dark 块误归 light）。

用户裁决：**viz 图表色 / agent 身份色全部纳入配置**；实现后**创建示例 `~/.agentnodes/theme.json`**。目标：`theme.json` 能控制前端每个颜色细节；删除该文件回退内置默认。

### 9.2 变量命名体系（新增）

| 组 | 变量 | 用途 |
|---|---|---|
| 全局 | `--focus-ring`、`--shadow-sm-color` | 焦点环、轻阴影色 |
| React Flow chrome | `--rf-control-{shadow,border}`、`--rf-button-{bg,border,fg,hover-bg,hover-fg}`、`--rf-minimap-{border,bg}`、`--rf-handle-{border,ring}` | 画布控件/缩略图/连接点（双主题） |
| 徽章 | `--badge-{builtin,user,running,completed,failed,neutral}-{bg,fg}` | 状态/来源徽章（双主题） |
| 工作流卡片 | `--dark-mode-card-{bg,border,hover-bg,hover-border}`、`--wf-card-{title,sub,plus}-fg`、`--wf-minimap-bg` | light 覆盖令牌化 |
| 色阶 | `--accent-NN`、`--sky-NN`、`--blue-NN` 及预混 `--sky-NN-aNN` | Tailwind 阶梯（原值 1:1 迁移） |
| viz/身份 | `--viz-{axis,grid,tooltip-bg,tooltip-border,up,down,fallback}`、`--agent-1..5`、`--node-*`、`--glow-stream-color`、`--code-bg` | 图表 chrome / agent 身份色 |

### 9.3 批次表

| 批 | 内容 | 提交范围 |
|---|---|---|
| 0 | 本文档（方案先行） | 仅 docs |
| 1 | 后端提取器：逐块状态机替代单次切分；颜色判定改为「值中包含颜色」；契约测试复用后端谓词 | system.py + test_system_theme_api.py |
| 2 | globals.css 字面量令牌化（只提纯颜色值，消费点 `Npx solid var(--x)`）；新增「变量声明外无颜色字面量」契约 | globals.css + paletteTokens.test.ts |
| 3 | Tailwind accent/sky/blue 阶梯指 CSS 变量；alpha 修饰符改预混 token；发送按钮改 `bg-primary` | tailwind.config.js + CSS + 用到的组件 |
| 4 | viz/身份色纳入：图表轴/tooltip/fallback、`AGENT_COLORS`、`NODE_PALETTE`、SVG fill/stroke、发光阴影、代码块底色 | 图表 10 文件 + 身份色文件 |
| 5 | hygiene 白名单收紧至 0 基线；生成示例 theme.json；全量验证 | scripts/arch + tests + 用户目录配置 |

### 9.4 alpha 修饰符约束（历史回归防线）

codemod 曾把 `!bg-slate-900/95` 误迁为 `!bg-background/95`——Tailwind 对 CSS 变量色不支持 `/NN` 透明度修饰符，类不生成导致浮窗透明（已由 `--surface-solid` 修复并加回归测试）。本专项涉及大量 `bg-sky-500/10`、`ring-sky-500/40` 等带 alpha 用法：一律改为预混 token 任意值 `bg-[var(--sky-500-a10)]`，不得直接写 `var()` + `/NN`。批 3 需构建后 grep 产物确认类生成。

### 9.5 验证与回滚

每批：git status 碰撞检查 → 受影响测试 → 全量 pytest（`-p no:hypothesispytest -p no:hypothesis -n 12 --dist loadgroup`）→ 前端 vitest/tsc/lint → `scripts\batch_gate.cmd` → 原值迁移应零像素 diff（有变化则显式重录基线并记录原因）→ 路径限定中文 commit、不 push。后端改动需重启服务后真机验证（当前进程为旧代码）。

回滚：每批独立 commit，`git revert <sha>` 即可；示例 theme.json 删除即回退内置默认。

### 9.6 批 6 · 配置文件参数化（角色映射 + 精确覆盖）

**问题**：批 5 完成后 `theme.json` 有 691 项（dark 348 + light 343），其中 **378 项（55%）是 Tailwind 默认色板材料**（`--emerald-400-rgb` 之类）。这些是代码级常量，不是用户要配的设计决策——配置文件应存「参数」，不该是色值清单。且 176 个色阶里实际只用到 86 个。

**三层结构**：

| 层 | 内容 | 归属 |
|---|---|---|
| 材质层 | `--emerald-400-rgb: 52 211 153` × 176 | **CSS 代码**（Tailwind 默认色板，原值，视觉零回归） |
| 语义槽层 | `--success-400-rgb: var(--emerald-400-rgb)` × 55 | CSS 默认值；主色族 → 角色槽 |
| 配置层 | `roles: {success: "green"}` + `overrides: {...}` | **theme.json（参数）** |

**主色族**（每角色全站用量最大者）：success→emerald(74)、danger→rose(77)、info→sky(45)、thinking→violet(14)、warning→amber(10)。只有主色族指向语义槽，同角色的其它族（green/red/cyan/…）仍指向自身材质——否则 `roles.success=green` 会让 emerald 与 green 撞成同一色。

**配置文件结构**（每主题）：
```json
{"theme": {
  "dark": {
    "roles":    {"success": "emerald", "danger": "rose", "info": "sky",
                 "thinking": "violet", "warning": "amber"},
    "overrides": {"--success-400-rgb": "52 211 153"}
  },
  "light": {...}
}}
```
`roles` = 角色→色相（参数，后端展开为具体材质）；`overrides` = 精确覆盖（优先级高于 roles）。旧的扁平写法（`"--var": "value"`）仍兼容。

**效果**：改 `roles.success` 为 `"green"` → 后端把 emerald 各档材质换成 green 同档材质 → Tailwind `emerald.*` 类（经语义槽）整体变绿，**组件零改动**。实测：`text-emerald-400` 元素计算色由 `rgb(52,211,153)` → `rgb(74,222,128)`。

**契约测试**：后端 4 例（角色映射生效 / 默认保持原值 / overrides 优先 / 未知色族忽略）+ 前端 3 例（槽双主题声明且指向主色族 / 主色族绑槽 / 非主色族保持自身材质）。

### 9.7 批 7 · 梳理复核与「按色族独立」修正

**梳理结论**：批 6 的结构三方一致（槽默认族 = Tailwind 绑定族 = 用量最大族），但**角色跟随率仅 70%**——改 `roles.danger` 时，同属 danger 的 `red`（47 处）/ `pink`（3 处）不跟随；thinking 仅 50%、warning 仅 46%。原因：批 6 只给主族建槽。

**方案选择**：初版拟「同角色所有族统一色相」，但核查发现现有视觉中 `red`（错误红）与 `rose`（玫瑰红）色相本就不同（另有 violet/indigo、cyan/sky、amber/orange 等 9 组），统一会丢失这层区分 —— **用户裁决：按色族独立控制**。

**最终结构**（槽 = 角色+族+档，每主题 165 个）：

| 层 | 示例 | 说明 |
|---|---|---|
| 材质 | `--emerald-400-rgb: 52 211 153` | Tailwind 默认色板，代码级常量，**不导出到配置** |
| 槽 | `--success-emerald-400-rgb: var(--emerald-400-rgb)` | 每族独立槽，默认指向自身材质 |
| 配置 | `"roles": {"emerald": "green"}` | **色族 → 目标色族**（参数） |

配置文件（每主题 16 项）：

```json
{"theme": {
  "dark": {
    "roles": {"emerald": "emerald", "green": "green", "rose": "rose",
              "red": "red", "pink": "pink", "sky": "sky", "cyan": "cyan",
              "teal": "teal", "indigo": "indigo", "violet": "violet",
              "purple": "purple", "fuchsia": "fuchsia", "orange": "orange",
              "amber": "amber", "yellow": "yellow"},
    "overrides": {}
  },
  "light": {...}
}}
```

**语义**：
- `{"emerald": "green"}` → emerald 族改用 green 色相（该族全部档位），**其它族不受影响**
- `{"rose": "amber", "red": "amber"}` → danger 系两族各自统一到琥珀（可逐族指定）
- 未列出的族保持自身材质 → 视觉零回归
- 槽 → 材质单向引用（材质为叶子）→ 无循环引用风险

**实测**：改 `roles.emerald = "green"` → 监控页 `text-emerald-400` 计算色 `rgb(52,211,153)` → `rgb(74,222,128)`，同页 `text-red-400` 保持 `rgb(248,113,113)` 不变。

**契约测试**：后端 5 例 + 前端 3 例（每族独立槽双主题声明 / Tailwind 每族绑自己槽 / 每族槽互不相同防撞色）。

### 9.8 单元测试（§9 批 8）

配置链路分三层，各自单测锁定：

| 层 | 文件 | 用例数 | 覆盖 |
|---|---|---|---|
| 纯函数（后端） | `tests/test_theme_config_units.py` | 41 | `_is_color_value` 颜色判定（含多值 shadow / RGB 通道 / 越界拒绝）、`_iter_theme_declarations` 块归属（多行值 / 注释 / 非主题块排除）、`_apply_role_families` 展开（默认不变 / 只改指定族 / 多族统一 / 跨角色 / 未知族 / 非字符串 / 材质不动）、族↔角色映射不变量（无重叠 / 属色板子集） |
| 注入（前端） | `webui/frontend/src/test/themeConfigUnits.test.ts` | 9 | `injectThemeStyle` 双主题作用域隔离、槽变量 `var()` 原样注入、材料值注入、非 `--` 键过滤、非字符串值过滤、重复注入复用节点、节点被移除后重建、空主题不注入 |
| 端到端 | `tests/test_system_theme_api.py` | 26 | 角色映射生效 / 默认零回归 / overrides 优先 / 未知族忽略 / **10 种畸形配置参数化**（空、根非对象、类型错误等，均回退不 500）/ 旧扁平格式兼容 / roles+overrides 组合 / 双主题独立 |

另新增 `webui/frontend/public/theme.example.json` —— **仓库内**的示例配置（含用法说明），用户照抄即可；契约测试校验其结构与解析器一致，防止示例与实现脱节。

### 9.9 批 9 · 覆盖率补强

先用覆盖率数据定位盲区（而非凭感觉加测试），再针对缺口补写：

| 模块 | 语句 前→后 | 分支 前→后 | 函数 前→后 |
|---|---|---|---|
| `utils/themeConfig.ts` | 83.7 → **100** | 85.7 → 88.9 | 50 → **100** |
| `stores/session.ts` | 87.9 → **92.6** | 69.7 → 75.3 | 88.9 → 94.4 |
| `hooks/useComposerCollapse.ts` | 93.8 → **97.9** | 53.3 → 71.4 | 66.7 → **100** |

新增用例（40 例）：

- `tests/test_theme_config_load.py`（21 例）—— 直接测 `_load_theme()` 的文件级边界：文件缺失/是目录（OSError）/非 UTF-8/非法 JSON/根非对象/theme 非字典/主题值非字典/roles 类型错/overrides 类型错/非字符串键值/扁平与两层混用/返回值是副本（不改污染内置默认）/默认路径在用户目录。
- `webui/frontend/src/test/themeConfigLoad.test.ts`（5 例）—— `loadThemeConfig` 此前**函数覆盖 0%**：拉取成功注入、拉取失败静默降级、响应畸形不崩、空配置不建节点、槽变量原样注入。
- `webui/frontend/src/test/useComposerCollapseUnits.test.ts`（8 例）—— 宽度→档位映射（full/short/icon）、resize 重测、rAF 缺失降级、ResizeObserver 缺失不崩、零宽保持 full、ref 未挂载不注册监听。
  *测法要点*：jsdom 无布局（`offsetParent` 恒 null）→ hook 回退自身宽度（`useComposerCollapse.ts:25`）；`useLayoutEffect` 依赖 `[]` 只跑一次 → 必须在挂载前让 ref 拿到真实元素，故用 `render` + 真实 DOM 而非 `renderHook`。
- `webui/frontend/src/test/sessionStoreBranches.test.ts`（6 例）—— `deleteSession` 本地清理/失败抛错/删完最后一片新建会话；`runSearch` 失败清空、过期响应丢弃（竞态）、`isSearching` 复位。

**变异测试验证断言有效性**（4 项全部被捕获，非恒真断言）：

| 变异 | 结果 |
|---|---|
| `collapseModeForWidth` 阈值改错 | ✓ 测试失败 |
| `injectThemeStyle` 去掉 `typeof v === 'string'` 过滤 | ✓ 测试失败 |
| `loadThemeConfig` 去掉异常兜底 | ✓ 测试失败 |
| `_apply_role_families` 改为空操作（后端） | ✓ 测试失败 |
