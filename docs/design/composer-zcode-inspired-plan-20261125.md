# AgentNodes 输入框分阶段改造方案（借鉴 ZCode）

> 2026-11-25 · 基于已核实的 ZCode bundle 证据 + AgentNodes Composer 现状基线。
> 方案先行，批准后才动代码。

## 一、现状基线（只读核实）

### AgentNodes `Composer.tsx`（494 行）

```
外层 p-4 容器
├─ 图片附件预览行（mb-2 flex gap-2，h-16 w-16 缩略图 + 删除钮）
├─ SlashCommandMenu（锚定工具条上方）
└─ 卡片 glass rounded-2xl px-4 py-3（focus-within:border-sky-500/60）
   ├─ 编辑器行 flex items-end gap-2
   │  ├─ textarea rows=1 resize-none flex-1（JS 自动增高）
   │  └─ trailing actions flex gap-1：上传图片(ghost) + 发送/停止(圆形实心)
   └─ ComposerStatusBar（persona / 模式 / 模型 / 思考 / 用量环 / 状态）
```

**已具备**：圆形发送按钮（唯一实心焦点，bg-sky-500）、ghost 次要按钮、卡片 focus-within 高亮、JS 自动增高、草稿按 session 持久化、markdown 快捷键。

**缺口**（对照 ZCode 设计哲学）：
- 状态栏按钮多（persona/模式/模型/思考/用量），窄屏靠 `flex-wrap` 换行 → 卡片高度跳动，输入区被挤压
- 无三区 DOM 语义契约（leading/trailing 混在状态栏里）
- 无 @ 统一提及入口（文件/会话/插件分散）
- 无优先级折叠（宽度变化靠 CSS wrap，而非有序降级）

### ZCode 核实事实（`styles-a4AckxCl.js`）

1. `L3t({layout: 'stacked'|'inline', ...})` —— 双布局形态
2. `F3t()`：useLayoutEffect + ResizeObserver（观察容器 + 三区）+ MutationObserver（子节点变化）
   → `cloneNode(true)` 到隐藏层试算 → 同步折叠决策
3. `N3t()` 溢出量 = leadingContent 宽 - leadingActions 宽 - trailingActions 宽 - gap
4. `P3t()` 折叠顺序：`extension → 0 → 1 → 2 → 3 → provider-compact → thought-icon → model-icon`
5. 三区 data 属性：`data-composer-leading-actions / leading-content / trailing-actions`
6. 发送按钮：`rounded-full bg-brand text-foreground-inverse`，其余 `variant='ghost'`
7. `M3t()` @ 提及聚合：files/sessions/plugins 三类 → 单菜单；`+` 聚合 attach/goal/workflow

## 二、设计哲学评估（哪些值得借鉴）

| 哲学 | 价值 | 我们是否已有 | 结论 |
|---|---|---|---|
| 输入区优先，工具栏可牺牲 | 高（窄屏/长模型名时输入区不被挤） | 否（靠 wrap） | **借鉴理念** |
| 单一视觉焦点（发送实心圆钮） | 高 | 是 | 保持 |
| 次要动作 ghost | 中 | 是 | 保持 |
| 三区 DOM 契约 | 中（结构清晰、便于测量） | 否 | **借鉴结构** |
| 测量驱动折叠（clone+双Observer） | 高但成本也高 | 否 | **谨慎借鉴**（见风险） |
| @ 多源聚合入口 | 中（认知负担降低） | 否 | **可选借鉴** |
| stacked/inline 双布局 | 低（我们无侧边栏输入框场景） | 否 | 暂不做 |

## 三、分阶段方案

### 阶段 0 · 取证（已完成，问题属实）

三档视口 DOM 几何实测（1280×720 / 768×900 / 375×812）：

| 视口 | 卡片高 | 状态栏高 | 状态栏行数* | textarea 宽 | 文档横向溢出 |
|---|---|---|---|---|---|
| 1280×720 | 93px | **27px** | 4（top 值分组） | 524px | 无 |
| 768×900 | 158px | **92px** | 7 | 166px | 无 |
| 375×812 | 158px | **92px** | 7 | 173px | 无 |

\* 行数按子元素 top 坐标分组，含分隔点（1px div），实际按钮换行约 3 行。

**结论**：
1. **痛点属实**：窄屏状态栏高度 27px → 92px（3.4 倍），卡片 93px → 158px，输入区宽度被压到 166–173px（1280 时 524px）。这是真实的输入区挤压，不是假想问题。
2. **无横向溢出**：三档下 `card.scrollWidth == clientWidth`、`document.scrollWidth == clientWidth`——wrap 生效但代价是纵向膨胀。
3. **长 persona 名不是主因**：切到最长的「因子分析师」后，1280 仍 27px / 窄屏仍 92px，与「研究员」一致 → 换行主因是**总宽度不足**，不是单个按钮文字长度。因此阶段 1 的 CSS 断点方案可行性高；阶段 2 的测量驱动折叠可以暂缓。

截图证据：`gui-test-screenshots/composer_stage0_375px.png`
（模型不支持图片查看，视觉判断基于上述几何数据）

**取证后调整**：阶段 1 价值明确（减少纵向膨胀）；阶段 2 优先级下调——既然长名称不加剧溢出，CSS 断点 + 有序隐藏即可覆盖主要场景。

## 二点五、设计定稿（2026-11-25 · 用户确认，mockup 已验收）

经交互原型迭代（A/B/C 合并方案全部否决 → 回退状态栏设计 → 定稿调参），最终设计如下。
mockup：`docs/design/composer-stage1-mockup.html`（可交互），截图 `gui-test-screenshots/composer_restored_preD.png`。

**布局**（保留状态栏，方案 D"清空状态栏"被否决）：

- 圆环 = 上下文占用进度环，**绝对定位卡片右上角**；hover 向上弹出七段明细 + 数值 + 数据来源；低占用给 4% 最小可见弧
- 发送按钮 = 唯一实心圆钮，**绝对定位卡片右下角**；textarea 预留右侧 40px
- 状态栏**左右分组**：
  - 左组：persona · 模式（Build/Chat）
  - 右组：模型 · 思考（effort）——`margin-left:auto` 靠右，与发送按钮同一行相邻（中间 36px 为发送预留位）
- 右组菜单向左展开；所有 pill 可点击，组内单选，选中后同步 pill 文字（模型同步 full/short 双标签）

**定稿 token**：`--gap: 3px` / `--pill-h: 36px` / `--radius: 20px`（用户在设计面板调参确认）

**折叠规则（按卡片实测宽度，非 CSS 视口断点）**：
- `> 520px` 全展开
- `≤ 520px` 隐藏思考 + 模型改短名
- `≤ 360px` 仅图标
- 圆环与发送任何档位保留；折叠项保留 title 可达

**实施修订**（相对原阶段 1/2 划分）：
1. 折叠采用**卡片实测宽度**（`useComposerCollapse` hook：RO 观察卡片 + rAF/120ms/resize 兜底，
   jsdom/SSR 宽度不可得时保持 full），不走纯 CSS 断点——因滑块演示证明"滑块值 ≠ 真实渲染宽"。
   不做 cloneNode/双 Observer（维持阶段 2 简化承诺）。三区 data 属性已落 DOM
   （`data-composer-leading-actions / leading-content / trailing-actions`）。
2. 用量环从状态栏**移到卡片右上角**（Composer.tsx），状态栏不再渲染环；`isStale` 警示随环迁移
3. `ComposerStatusBar` 已有的 persona/model/thinking 下拉逻辑保留，仅重排为左右分组 + 接入折叠
4. 定稿 token（gap3/pill36/radius20）是 mockup 设计面板参数；真实代码沿用既有 Tailwind token
   （`gap-2` / `py-1` / `rounded-2xl`），视觉比例一致，未硬编码 mockup 数值

**落地后实测**（2026-11-25 真机）：
- 首次加载：1280 卡宽 596px → `data-collapse=full`；375 卡宽 245px → `data-collapse=icon` ✅
- 全量测试 1455 passed / tsc clean / build 成功
- 注：Playwright `setViewportSize` 热切换视口不触发 React 更新（测试环境特性）；
  真实浏览器 resize 事件有同步兜底，且首次加载判定正确

### 阶段 1 · 低成本结构性改造（推荐先做）

目标：引入三区语义，不引入 observer 复杂度。

1. Composer 卡片内加三区 data 属性：
   - `data-composer-leading-actions`：状态栏左组（persona / 模式）
   - `data-composer-leading-content`：textarea 行（保持 flex-1，不可挤压）
   - `data-composer-trailing-actions`：发送按钮
   - 把 `ComposerStatusBar` 拆成 leading/trailing 两个 slot 注入（或加 `slot` prop）
2. CSS 层实现"优先级折叠"的简化版：
   - 用 Tailwind `max-sm:hidden` / `lg:inline` 等断点，按 persona → 思考 → 模型别名 → 模型图标的顺序隐藏次要项
   - 保留核心：模式、模型、发送、用量环
3. 最小可见性：任何折叠后仍有 tooltip 可达入口（标题属性保留）

**改动面**：Composer.tsx + ComposerStatusBar.tsx + CSS，约 3 文件；测试补窄屏断言。

### 阶段 2 · 测量驱动折叠（仅当阶段 1 的 CSS 断点不够时）

对齐 ZCode `F3t/P3t`，但简化实现：
- **不做 cloneNode**（维护成本高、SSR/测试不友好）
- 用单个 `ResizeObserver` 观察卡片容器，读 `scrollWidth > clientWidth` 判定溢出
- 按优先级逐级给按钮加 `data-compact` 属性（extension→0→1→2→3 简化为 persona→思考→模型文字→模型图标）
- 每级折叠后重新测量，直到不溢出或全部折叠

**改动面**：新增 `useComposerCollapse` hook + Composer/StatusBar 接线 + 测试。约 4 文件。

**风险控制**：
- ResizeObserver 回调必须做防抖/节流（ZCode 用 rAF `I3t`）
- 折叠状态不应写入全局 store（纯视觉，局部 state）

### 阶段 3 · @ 统一提及入口（可选，独立价值）

- 把现有附件按钮扩展为 `+` 菜单（附件 / 会话 @ / 插件）
- textarea 输入 `@` 触发聚合面板（files/sessions/plugins）
- 与现有 SlashCommandMenu 共存：`/` 命令、`@` 提及，语义分离

**改动面**：新组件 `MentionMenu.tsx` + textarea onChange 钩子 + 测试。约 4 文件。

### 阶段 4 · 不做（明确边界）

- stacked/inline 双布局：我们没有侧边栏输入框，无场景
- 100% 复刻 ZCode 的 cloneNode+MutationObserver：过度设计
- 更换发送按钮视觉：已对齐

## 四、验证方式（每阶段）

1. 视觉：375px / 768px / 1280px 三档截图 + DOM 几何（输入区高度、状态栏行数、是否溢出）
2. 功能：发送/停止/附件/slash 在各档位正常
3. 测试：Vitest 窄屏断言 + 全量回归
4. 真机：合并模式后端 + 浏览器 hover/输入验证

## 五、风险与取舍

1. 折叠系统的复杂度收益比：ZCode 是 IDE 级客户端才需要 clone+双Observer；我们阶段 1 的 CSS 断点方案可能在长模型名场景下不够准，届时再上阶段 2。
2. 状态栏按钮语义不同：ZCode 的 extension 按钮可牺牲，我们的 persona/模型是高频操作，折叠顺序需重新评估（建议 persona 优先级高于模型图标）。
3. 阶段 3 的 @ 提及需要后端支持（会话列表/文件列表已有 API，插件系统待确认），可能不止前端改动。

## 六、待裁决

1. 是否先执行阶段 0 取证（推荐）？
2. 阶段 1 的折叠顺序：persona → 思考 → 模型文字 → 模型图标，是否符合产品预期？
3. 阶段 3 的 @ 提及是否要做（需先确认后端插件 API 现状）？

## 七、视觉对齐批次实施记录（2026-11-26 · 用户批 A/B/D/E/F）

**依据**：ZCode 桌面版 `app.asar` 源码级调研（composer React+Tailwind 真实数值）+ 当前界面 DOM 实测，
发现 3 个实现缺陷（环传 `EMPTY_SEGMENTS` 分段从未生效 / 底环 `var(--muted)` 深色不可见 /
发送钮 `bg-sky-500` 绕过语义令牌）。用户裁决：做 A/B/D/E/F，不做 C（环保留右上角，不移工具栏）。

| 项 | 改动 | 文件 |
|---|---|---|
| A 分段环修复 | `segments={EMPTY_SEGMENTS}` → `segments={breakdown}`（环按 7 段 viz 令牌着色） | Composer.tsx |
| B 环对齐 ZCode | 描边 3→4（≈ZCode 4/24 比例）；底环 `var(--muted)` → `currentColor`+`strokeOpacity 0.25`；svg 文字色随 tone（normal=secondary / warn / danger）→ 深色可见、紧张变警示色 | ContextRing.tsx |
| D 发送钮令牌化 | `bg-sky-500 hover:bg-sky-400` → `bg-accent-500 hover:bg-accent-400`（accent-500=sky-500 同值，零视觉变化，theme.json 可调） | Composer.tsx |
| E 外壳质感 | `.composer-shell`：squircle 渐进增强（`@supports corner-shape` → 24px，回退 18px）；`.shadow-composer`（`--shadow-composer-soft`：亮色柔投影 `0 1px 3px+0 6px 18px rgba(0,0,0,.05)`，暗色 `0 0 #0000` 无阴影——ZCode 同款分工）；focus-within 边框 `info-fg/60`→`border-accent`（=brand）+ 背景升 `surface-raised` | Composer.tsx + globals.css |
| F 芯片统一 | 模式芯片去边框/去填充（原绿框高饱和抢层级），改与 persona/模型/思考同款中性形态，仅图标保留语义色 | ComposerStatusBar.tsx |

**B 未做 title**：环已有 hover 七段明细弹层（含数值/来源），原生 title 会与之双重量显示，故不加。

**验证**：前端 1511 passed（新增 2 契约：分段弧真实渲染 + 底环 currentColor/0.25/stroke4）/
后端 8265 / tsc clean / lint 0 error / gate ALL GREEN / build 成功。
真机双主题截图：环清晰可辨（紫色分段=系统提示，与弹层图例对应）、Build 芯片统一、
squircle 24px 生效（实测 computed radius 24px）、暗色无阴影亮色柔投影。
