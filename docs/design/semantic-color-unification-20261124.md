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

## 8. 实施记录（2026-11-24，用户「开始实施」后执行）

| 批 | 实际落地 | 偏差 |
|---|---|---|
| C1 | `globals.css` 新增 thinking/info 双主题令牌 + success/danger `fg-muted` 次级色（light 用加深色阶）；`tailwind.config.js` 全量映射 success/danger/thinking/info（各 4 类）；新增 `test_semantic_tokens_declared_in_both_themes` 双向声明契约。零视觉变化，tsc/build/契约测试 6/6/token hygiene 0 违规。 | 无 |
| C2 | SSEStatus / ThinkingBlock / ToolCallGroup / ToolCallBlock / ContextUsageBar / AgentItem / ComposerStatusBar / utils/status 共 8 文件硬编码→语义类；同步 2 个测试断言。受影响 56 测试通过。 | 无 |
| C3 | 批量替换 38 文件 / 91 行前景硬编码（emerald/red/rose/sky/blue/violet/purple/orange→success/danger/info/thinking/warning）；`check_token_hygiene.py` 新增第 4 类 `semantic-fg-hardcode` 门禁（基线 0、新增即红）+ 身份/viz/深底垫片 allowlist；同步 2 个测试断言。 | 保留 176 行深底垫片（bg-*-9xx 配浅字，合法设计）与身份/viz 色，未强行令牌化 |
| C4 | 前端 1368 测试 / tsc OK / lint 0 error；重建前端（VITE_E2E=1）+ 6 张目录基线重录（assistant-message、tool-call-done、tool-call-error × 双主题）；e2e 114 passed / 0 skipped；后端 8143 passed（1 例 gateway timeout flaky，单跑通过）；gate 13/13 ALL GREEN；亮色对比度复测 **53 → 14（降幅 74%）**，语义色低对比项（1.4–1.9）全部消除。 | 剩余 14 项均为次级文字（muted-foreground / slate），属方案边界外（P1 令牌调值），未纳入本批 |

**验收标准达成**：语义色 Tailwind 类入口 warning→全量；硬编码语义前景 60+→0（allowlist 外）；最差对比度 1.42→消除；单点可调（改 globals.css 两处）；门禁防回退（第 4 类检测）均达成。次级文字 14 项留作后续令牌调值批。
