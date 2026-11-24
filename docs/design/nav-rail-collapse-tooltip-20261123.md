# 图标栏折叠 + 统一 Tooltip 方案（R-014 · 待批准 · 零代码）

> 日期：2026-11-23
> 状态：方案。批准后才动代码；本文不改任何实现。
> 触发：`sidebar-navigation-redesign-20261123.md` 落地后，图标栏固定 64px、5 个入口均使用 HTML `title` 原生 tooltip；调研 `ux-side-panel-industry-survey-20261123.md` §M3/M4 建议「rail 两态折叠 + 一致 tooltip + 键盘可达」为后续可做项。

## 1. 现状（已自证）

- `IconNav.tsx`：`NAV_ITEMS` 2 项（Chat / Trace）+ 底部账户与登出，共 4 个按钮 / NavLink；全部用 `title` 属性做原生 tooltip。
- 图标栏宽度 `w-16`（64px）固定，窄屏和宽屏一致；无展开态。
- 唯一自定义 tooltip 是品牌 Logo 按钮（`group-hover` 浮层），与其余入口的原生 `title` 不一致。
- 无障碍：`NavLink`/`button` 有 `title`，但折叠态若只显示图标，`title` 是唯一标签来源；需保证键盘 Tab 与读屏可达。
- 无现成 IconNav 专项单测；契约断言在 `tests/test_sidebar_navigation.py`（Python 文本断言）+ 基线 10 张导航相关截图。

## 2. 方案（最小改动）

### 批 R1：折叠状态与开关

1. `stores/layout.ts` 新增 `railCollapsed: boolean`（默认 `false`，持久化沿现有 layout persist 方案）+ `toggleRail()`。
2. `IconNav.tsx` 顶部加一个折叠按钮（ChevronLeft / ChevronRight），点击切 `toggleRail`。
3. 宽度切换：`w-16` ↔ `w-44`（或 `w-48`）；展开态每个入口显示 `icon + label` 文本，折叠态只显示图标。
4. `NavLink`/`button` 保留 `title`（键盘/读屏标签不丢）。

### 批 R2：统一 Tooltip（折叠态）

1. 抽一个 `RailTooltip` 小组件：折叠态在 hover / focus 时于图标右侧显示浮层（复用 Logo 现有视觉：rounded-md / border-slate-700 / bg-slate-800 / text-[11px] / shadow-lg），展开态不渲染。
2. 4 个入口 + Logo 全部套用；删除 Logo 内联 tooltip 代码（单一实现）。
3. tooltip 由 `group-hover` + `group-focus-within` 触发，键盘 Tab 时同样出现。

### 批 R3：测试与基线

1. 新增 `webui/frontend/src/test/iconNav.test.tsx`：折叠切换、展开态 label 可见、折叠态 label 隐藏但 `title` 保留、键盘 focus 出 tooltip、路由仍正确。
2. `tests/test_sidebar_navigation.py` 增 1 条断言： IconNav 含 rail 折叠状态消费（防回退）。
3. 受影响测试 → 前端全量 / tsc / lint → 后端全量 → gate 13/13 → 双主题基线重录（导航相关场景）。

## 3. 不变式

- 导航目标不变：`/chat` `/trace` `/account` `/login`（登出）；`toggleSidebar` 行为不变。
- Logo 首页跳转不变；`Chat` 在当前路由再次点击 toggleSidebar 不变。
- 不引入新依赖；不升级 Tailwind（P1-1 硬约束）。
- 前端 rail 折叠与 `sidebarOpen`（会话侧栏）正交，互不联动。

## 4. 不做

- 不改 Navigator 内容（不加 Agents / Files / Automation 入口，那些需后端契约）。
- 不改 `NAV_ITEMS` 顺序与图标语义。
- 不做拖拽调宽、不做动画过渡时长定制（沿用 transition-colors 即可）。

## 5. 验收

- 默认 64px 视觉与现状一致；点折叠按钮切到 176px 展开态，4 个入口文本可见。
- 折叠态 hover / 键盘 Tab 时 tooltip 出现在图标右侧；展开态无浮层干扰。
- 前端全量测试 / tsc / lint 绿；后端全量 / gate 13/13 绿；基线重录自证稳定。
- 实机（Vite 5173）验证两态切换 + 键盘可达。

## 6. 回滚

`git revert` 本批提交；layout store 新字段随提交回退，persist 旧值被忽略，无迁移成本。
