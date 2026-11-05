# 右侧面板由 Agent 主动驱动（show_chart / show_report）

日期：2026-08-10
状态：已实施
相关模块：`core/agent/builtin_tools/display_tools.py`、`core/agent/tools.py`、`core/agent/loop.py`、`core/backtest.py`、`api/session/projector.py`、`webui/frontend/`

---

## 背景

「表现曲线」卡原本完全被动：后端 AgentLoop 从不 emit `chart` 事件，前端只能从
`run_backtest` 工具结果的稀疏指标做 fallback，曲线永远为空。

目标：**由 chat agent 决定右侧面板显示什么**——图表（`show_chart`）或 HTML 分析
报告（`show_report`），双显（聊天消息流 + 右侧面板最新一个）。

## 数据流

```
run_backtest ──► 导出 runs/<s>/<run>/equity_curve.csv（nav，数据不进上下文）
                 └─► 返回 artifacts 路径引用（agent 只拿到路径）
agent ──► show_chart(source_file, chart_type, title)
            ├─ 读文件（CSV/JSON）→ 等距采样 ≤500 点
            └─ emit chart SSE ──► 聊天流 ChartBlock + 右侧 PanelRenderCard
       ──► show_report(strategy_name, run)
            ├─ report.html 不存在则按需生成（内联 SVG + 指标卡，离线可用）
            └─ emit html SSE ──► 聊天流 HtmlBlock + 右侧 sandbox iframe
```

## 关键设计

### 1. nav 不进 LLM 上下文（文件引用式）
- `run_backtest` 导出 `equity_curve.csv`（date/nav）到 run 目录（`core/backtest.py`）
- 工具返回只含 `artifacts` 相对路径引用；净值序列数据本身从不进上下文
- agent 展示时传路径（`show_chart(source_file=...)`），数据在工具内部读取，只经 SSE 传前端

### 2. 工具 → SSE 的发射路径
- `ToolContext` 新增 `emit_event`（接 `AgentLoop._emit`）与 `message_id`（当前 assistant 消息）
- loop.py 两处 ToolContext 构造注入（`emit_event=self._emit`, `message_id=getattr(self, "_current_message_id", None)`）
- show_chart / show_report emit 时带 `message_id` → projector `_ensure_assistant_message` 把 part 挂到正确的 assistant 消息

### 3. 持久化（刷新后仍在）
- `event_v2.py` 加 `HTML = "html"`（CHART 已有）
- projector `_on_html`：html part（content 全文持久化，不依赖文件存在）
- chart part 复用既有 `_on_chart`
- SSE 事件 id（`chart-<uuid>` / `report-<uuid>`）+ part 幂等（同 id 不重复追加）

### 4. 前端
- `HtmlBlock.tsx`：sandbox iframe srcdoc 渲染（sandbox="" 禁脚本，不用 dangerouslySetInnerHTML）
- `ChartBlock.tsx` 导出共享 `ChartRenderer`（面板复用）
- `utils/equityCurve.ts` 加 `extractLatestPanelItem(messages)`：取最近一个 chart/html part
- `PanelRenderCard.tsx`：「表现曲线」卡的唯一容器；直接持有：
  - chart part → `ChartRenderer`
  - html part → sandbox iframe
  - 无展示物但有 `run_backtest` 指标 → 指标行（total_return / sharpe / max_drawdown）
  - 都无 → "暂无回测净值数据" 占位
- `GoalCard.tsx`：纯 GoalTab 容器，不再内嵌 EquityCurveCard（消除标题重复）
- SSE `html` handler（`messageHandlers.ts`，走 attachBlockPart 模式）
- 「目标 & 进度」卡只渲染 GoalTab（被动跟踪，见 `docs/archive/undated/goal-events-panel-link.md`）

### 5. 安全
- 路径穿越防护：`_resolve_workspace_file` 用 `resolve().is_relative_to(workspace)` 校验
- HTML 一律 sandbox iframe；工具只读工作区内文件
- 数据采样 ≤500 点（SSE + DB 大小可控）

## 配置与边界
- 无新配置项（采样 500 点硬编码；后续可参数化）
- 角色白名单：`strategist` / `backtest_diagnostics` / `researcher` 已加 `show_chart`；
  `strategist` / `backtest_diagnostics` 加 `show_report`
- `effects`：show_chart 只读；show_report 声明 fs（按需生成 report.html）

## 测试
- `tests/test_display_tools.py`（18 个）：读文件/采样/emit/穿越/缺文件/JSON 源/无回调、
  报告生成与复用、_on_html 持久化幂等、SVG 渲染
- 前端 `PanelRenderCard.test.tsx`（8 个）：extractLatestPanelItem 选择逻辑、chart/html 渲染、
  sandbox iframe、metrics fallback
- vitest 716/716、tsc 0 错误、pytest 子集零新增失败

## 变更记录

### 2026-08-10 — GoalCard 拆分 + EquityCurveCard 内联
- **症状**：右侧面板出现两个相同的「表现曲线」标题
  - 第 1 个：GoalCard 内部嵌套的 EquityCurveCard（曲线/metrics）
  - 第 2 个：PanelRenderCard fallback 调用的 EquityCurveCard
- **修复**：
  1. `GoalCard` 改为纯 GoalTab 容器（去掉 `curve` / `metrics` props，去掉内嵌
     EquityCurveCard / 分隔线）
  2. 把 EquityCurveCard 的曲线/指标 fallback 逻辑全部内联进 `PanelRenderCard`，
     PanelRenderCard 直接渲染 recharts LineChart + 指标行 + 占位
  3. `RightPanel` 接线更新：`GoalCard` 不再传 `curve={null} metrics={null}`
  4. `RightPanel.test.tsx` 标题断言收紧：`getAllByText(≥1)` → `getByText(唯一)`
  5. `utils/equityCurve.ts` 注释微调：`BacktestMetrics` / `extractLatestBacktestMetrics`
     现在服务于 `PanelRenderCard` 而非 `EquityCurveCard`
- **保留**：`EquityCurveCard.tsx` 作为废弃文件保留（不再被 import，git 历史可读；
  `extractEquityCurve` / `decodePoints` 仍保留为未来可用工具，目前无调用方）
- **Backend 不动**：投影 / 事件 / 工具 / SSE 均无变更
- **验证**：vitest 全过、tsc 0 errors
