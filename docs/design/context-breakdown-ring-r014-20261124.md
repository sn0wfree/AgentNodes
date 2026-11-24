# 7 段分段上下文用量 + 圆环呈现方案（R-014 · 待批准 · 零代码）

> 日期：2026-11-24
> 状态：方案。批准后才动代码；本文不改任何实现。
> 触发：用户问「7 段分段上下文用量实现了吗？」+「能否借鉴 zcode 圆环呈现，hover 显示 7 段统计」。
> 依据：`context-usage-agent-survey-20261124.md`、`composer-cd-subplan-20261124.md`（C-1/C-2）、`composer-layout-industry-benchmark-20261124.md`（ZCode 用量形态）、C/D 后端契约调研。

---

## 0. 现状（诚实交代）

| 项 | 状态 |
|---|---|
| 实测总量到前端 | ❌ **未通**：C-1 代码已改（`apps/chat/events.py` 直通集合加 `llm_usage`/`session_total_tokens`），但实机发消息后 `tokensUsed` 仍为空，事件在 Gateway 更上游缺失 |
| 7 段数据源 | ❌ **完全缺失**：后端没有任何分段记账（`meta_user_context`/`mcp_tool_schemas` 连概念都没有） |
| 前端用量 UI | ✅ 已细化（F4-b）：行内紧凑文本 + hover 详情，但显示的是**字符估算值**（`~` 前缀） |
| 圆环 UI | ❌ 不存在；前端无现成圆环组件（Spinner 是加载圈，不可复用） |

**结论**：没有数据，圆环只是空壳。方案必须 **先通数据、再做 UI**。

---

## 1. 目标

1. 让生产 chat 路径的**实测 token 总量**到达前端（C-1 排障修复）
2. 定义并实现 **7 段分段数据**（C-2）
3. 前端把用量控件改为**圆环**，hover 展开 7 段明细（渐进式披露），对齐 ZCode 的低调+详情分层理念

---

## 2. 批次

### 批 H0 · C-1 排障：让实测总量到前端（前置，必做）

**问题**：C-1 代码已改但实机无效。需定位事件在哪一层丢失。

**排查顺序**（每步用最小探针验证）：
1. **AgentLoop 是否 emit `llm_usage`**：在 `kernel/agent/loop/stream.py:102-103` 的 `_emit("llm_usage", usage)` 处临时加日志（或用现有 trace），发一条消息看日志
2. **Gateway sink 是否收到**：在 `kernel/gateway/session_controller.py:633` 的 `make_loop_event_sink(sink)` 包装处加透传日志
3. **bridge 是否转发**：`api/routers/chat.py:755-763` 的 `mapping` + `STREAM_PASSTHROUGH_KINDS`——确认 `llm_usage` 在集合内（C-1 已加）
4. **SSE 是否下发**：`_push_sse` 的过滤条件
5. **前端 handler 是否收到**：`hooks/sse/controlHandlers.ts:12-46` 加 debug

**可能的根因假设**（待验证，不预设结论）：
- 假设 A：Gateway 路径的 loop 用 `on_event=make_loop_event_sink(sink)`，但 `sink` 是 Gateway 内部 sink，不是 chat bridge 的 sink——即事件流进了 Gateway 而不是 SSE
- 假设 B：`apps/chat/send_turn.py` 的 `_event_bridge` 注册为 Gateway sink，但 bridge 的 `kind` 与 `llm_usage` 不匹配（大小写/前缀）
- 假设 C：MiniMax adapter 的 usage 解析未产出 `chunk.usage`（`openai_client.py:540` 的 DIAG 日志可验证）

**修复**：按根因最小改动，让 `llm_usage`/`session_total_tokens` 端到端到达前端 `tokensUsed`。

**验收**：发一条真实消息 → 前端 `useChatStore.tokensUsed` 非空 → 用量显示去掉 `~` 前缀（实测）。

### 批 H1 · C-2 定义并实现 7 段数据（依赖 H0）

**先决策**（用户需回答）：`meta_user_context` / `mcp_tool_schemas` 后端无对应概念 →
- 选项 A：**只做有数据的 5 段**（messages / system_prompt / skills / tool_prompt / system_tool_schemas），UI 显示 5 段
- 选项 B：后端新增 2 个概念的归属规则（工作量显著增加）

**实施**（以选项 A 为例）：
1. **后端分段计算**：在 `kernel/agent/telemetry/agent_telemetry.py:267-321`（已有 `system_prompt_len`/`tools_count`/`estimated_tokens` 长度指纹）扩展为按段估算 token：
   - `messages`：history content 长度 / 3.0（复用 `foundation/utils/token_utils.py:15`）
   - `system_prompt`：`system_prompt_len`
   - `skills`：`ContextItem.kind=skill`
   - `tool_prompt` / `system_tool_schemas`：从 tools schema 估算
2. **新事件**：`context.breakdown`，payload `{session_id, attempt_id, segments: {...}, total, context_window}`，注册进 `kernel/events/event_v2.py`
3. **bridge 放行**：加入 `apps/chat/events.py` 直通集合
4. **前端**：
   - 事件类型（`hooks/sse/types.ts`）+ handler（`controlHandlers.ts`）+ 注册（`handlers.ts`）
   - store 字段 `contextBreakdown: Record<string, number>`（`stores/chat.ts`）
   - 圆环 UI（H2）

**验收**：发消息后 `context.breakdown` 事件到达前端，5 段之和 ≈ 总量。

### 批 H2 · 圆环 UI + hover 7 段明细（依赖 H1）

**设计**（对齐 ZCode 的低调触发器 + 详情弹层理念）：

```
常态（状态行右侧）：
  ◔ 42%        ← SVG 圆环（直径 ~20px），conic-gradient 或 SVG stroke-dasharray 分段着色
                   hover 时向上弹层

hover 弹层（w-72）：
  ┌─────────────────────────────┐
  │ 上下文用量        420K/1.0M │
  │ ◕ 大圆环（直径 ~64px）       │  ← 分段着色：messages/system_prompt/skills/tool_prompt/schemas
  │   42%                       │
  │ ── messages        180K 43% │
  │ ── system_prompt    40K 10% │
  │ ── skills           12K  3% │
  │ ── tool_prompt      88K 21% │
  │ ── schemas         100K 24% │
  │ 剩余可用 580K               │
  │ 数据来源：实测 / 估算        │
  └─────────────────────────────┘
```

**实现**：
1. 新建 `components/chat/ContextRing.tsx`：纯 SVG 圆环组件
   - props: `segments: {key, label, value, color}[]`, `percent`, `size`
   - 用 `stroke-dasharray` / `stroke-dashoffset` 逐段绘制，每段一种 viz 令牌色（`--viz-1..8` 已存在）
   - 支撑 dark/light 双主题（viz 令牌已有双主题值）
2. 状态行 `ComposerStatusBar` 的用量区：把当前文本进度条替换为小圆环 + 百分比；保留 hover title 作为无 JS 降级
3. hover 弹层：复用现有 `glass` 样式 + 绝对定位 `bottom-full`（与 persona/model 下拉一致），显示分段列表
4. 右栏 `TokenCard` 同步显示圆环（可复用组件，size 更大）
5. `ContextUsageBar`（聊天流顶部）保留整条进度条形态，但用同一分段数据源着色

**测试**：新组件纯渲染测试（分段比例、颜色、hover 弹层）+ 现有用量测试回归 + 双主题基线重录

---

## 3. 验收标准

| 断言 | 前 | 后 |
|---|---|---|
| 实测总量到前端 | ✗ tokensUsed 恒空 | ✓ 发消息后非空，无 `~` |
| 分段数据 | ✗ 完全缺失 | ✓ 5（或 7）段事件到达前端 |
| 圆环 UI | ✗ 无 | ✓ 状态行小环 + hover 弹层大环 + 分段明细 |
| 主题 | — | ✓ dark/light 双主题令牌色 |
| 全量 | — | 前端测试 / tsc / lint、后端全量、gate 13/13、双主题基线重录、实机双宽度 |

---

## 4. 不做

- 不做 D（排队编辑态）——另案
- 不做语音/worktree/CUA
- 不在 H0 未通前先做圆环空壳

## 5. 风险与回滚

- **H0 最不确定**：Gateway 事件流缺失根因未明，可能需要改 L3 kernel；排查时间不可控。若根因是架构性（事件根本不该走 SSE），需重新评估方案。
- **H1 定义缺失**：5 段 vs 7 段需用户决策；若选 B（新增 2 概念），工作量翻倍。
- **H2 视觉**：圆环 + 弹层会改变用量基线，需重录；窄屏弹层需避免溢出。
- 每批独立提交，可单独 revert。

## 6. 用户决策（已定，2026-11-24）

1. **H1 段数**：**选项 B —— 做满 7 段**，后端新增 `meta_user_context` / `mcp_tool_schemas` 两个概念的归属规则
2. **圆环形态**：**先用小环**（状态行 ~20px），样式后续再调

## 7. H0 排障结论（2026-11-24 静态分析 + 单测）

**bridge 层确认是通的**：新增 `tests/test_usage_sse_bridge.py`，直接驱动生产 `apps/chat/send_turn.py::_event_bridge`，断言 `llm_usage` / `session_total_tokens` 经 `translate_engine_event` 放行后被 `publish_sse` 推送——**2/2 通过**。C-1 对 `apps/chat/events.py` 直通集合的修改是有效的。

**链路静态核对**：
- `AgentLoop._emit("llm_usage")`（`loop/stream.py:102-103`）→ `on_event=make_loop_event_sink(sink)`（`session_controller.py:633`）
- `sink` 在 `session_controller.py:550-554` 被包装为「自动加 event_correlation_id」→ AgentLoop 事件同样带 correlation
- Gateway `_combined_sink`（`gateway.py:263-299`）广播所有外部 sink → `send_turn._event_bridge` 注册在 `gw.add_sink(bridge)`（`send_turn.py:385-387`）
- bridge 的 correlation 过滤（`send_turn.py:352-355`）要求匹配本 turn 的 id → 包装后的 sink 满足
- `translate_engine_event` 放行 → `publish_sse(out_kind, payload, session_id)`（`send_turn.py:381`）

**结论**：从代码静态分析与单测看，**C-1 的事件链路是完整的**。此前实机 `tokensUsed` 为空，最可能的原因是：
- (a) 实机时后端进程未真正加载到 C-1 改动（服务重启时序/缓存）
- (b) MiniMax API 响应中 `chunk.usage` 未产出（`openai_client.py:540` 的 DIAG 日志可验证）
- (c) 浏览器 SSE 连接/handler 状态问题

**H0 不再盲改代码**；需在服务恢复后用 DIAG 日志做一次端到端确认（1 条消息即可）。若确认 (b)，则改 provider adapter 的 usage 解析；若 (a)/(c)，无需改代码。

**已提交**：`tests/test_usage_sse_bridge.py`（H0 契约测试，随本批提交）。

## 8. 实施记录（2026-11-24）

### H1 · 7 段数据（已完成）

- **后端**（选项 B，做满 7 段）：
  - 新建 `kernel/context/breakdown.py` —— 7 段归属规则的**单一事实源**，新增两个概念：
    - `meta_user_context` = ContextBundle 非 skill 条目（goal/todo/memory/hypothesis/knowledge/artifact/tool_result/history）——"系统注入的用户上下文"，非用户手写
    - `mcp_tool_schemas` = tool name 以 `mcp__` 开头或带 `mcp`/`server` 标记的工具说明+schema
  - `kernel/events/event_v2.py` 注册 `context.breakdown` 事件
  - `kernel/agent/telemetry/agent_telemetry.py` 在 `llm_request` 装配点 emit breakdown（**必须在 llm_request 之前**——多个测试以 `_emit.call_args`（最后一次）取 llm_request entry，wire 零变化）
  - `kernel/gateway/session_controller.py` 在 context 注入时保存 items 到 `self._last_context_items` 并挂到 loop 上供 breakdown 读取
  - `apps/chat/events.py` 直通集合加 `context.breakdown`
  - 6 个纯函数单测 + bridge 契约测试
- **前端消费**：
  - `stores/chat.ts` 新增 `contextBreakdown: Map<string, Record<string,number>>` + action
  - `hooks/sse/types.ts` 加事件类型 + ctx mutator；`controlHandlers.ts` 加 handler（解析 segments、过滤非数值）；`handlers.ts` 注册；`useSSE.ts` 接线
  - 3 个 handler 单测 + handlers registry 契约同步
- **验证**：后端全量 8159 passed（失败 3 例均已处理：架构数字同步 + `assert_called_once` 改按 kind 过滤）；前端 1426 passed

### H2 · 圆环 UI（已完成）

- 新建 `components/chat/ContextRing.tsx`（纯 SVG）：
  - `buildArcs()` 按 value/total 映射为 `stroke-dasharray/dashoffset` 分段弧，颜色用 viz 令牌（dark/light 双主题已有）
  - `ContextRing` 支持 size 参数（状态行 20px 小环 / 弹层 64px 大环）；无分段数据时单色环兜底
  - `BreakdownList` 显示 7 段明细（label + 绝对值 + 百分比）
- `ComposerStatusBar` 用量区：文本进度条 → 小圆环 + 估算/实测文本；hover 弹层（`glass` + `bottom-full` + `group-hover/usage`）显示大环 + 7 段明细 + 剩余可用 + 数据来源
- **修复的真实 bug**：`useChatStore((s) => s.contextBreakdown.get(id) ?? {})` 每次渲染创建新对象 → zustand selector 认为状态变化 → **无限重渲染**（`Maximum update depth exceeded`，22 个无关测试连带失败）。改用模块级稳定常量 `EMPTY_SEGMENTS`
- 8 个组件单测（buildArcs / 渲染分支 / BreakdownList）
- e2e 114 passed（圆环未改变基线场景）

### 全量验证

前端 1426 passed / tsc OK / lint 0 error；后端 8159 passed / 185 skipped / 2 xfailed；gate 13/13 ALL GREEN；架构数字随新增文件同步（L3 231 文件 / 51,697 行，total 466 / 97,736）

### 待实机确认（服务仍暂停）

- 发一条真实消息后 `context.breakdown` 事件是否到达前端（H0 结论：bridge 层已单测确认通，但 MiniMax usage 解析未实机验证）
- hover 小环弹层在双主题/窄屏下的视觉（当前基线未覆盖 hover 态）

## 9. 来源

- `context-usage-agent-survey-20261124.md`（用量全貌）、`composer-cd-subplan-20261124.md`（C-1/C-2 调研）、`composer-layout-industry-benchmark-20261124.md`（ZCode 用量弹层）
- 代码：`apps/chat/events.py`、`api/routers/chat.py`、`kernel/gateway/session_controller.py`、`kernel/agent/loop/stream.py`、`kernel/agent/telemetry/agent_telemetry.py`、`foundation/utils/token_utils.py`、`webui/frontend/src/hooks/useContextUsage.ts`、`webui/frontend/src/components/chat/ComposerStatusBar.tsx`
