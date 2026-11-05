# Goal 事件进消息 + 右侧面板联动（方案 A：全量快照事件驱动）

日期：2026-08-10
状态：设计中
相关模块：`api/session/`、`api/routers/chat.py`、`api/routers/goal.py`、`core/agent/`、`core/goal/`、`webui/frontend/`

---

## 背景与问题

### 1. 右侧面板与聊天无联动

右侧面板（`RightPanel.tsx`）的「表现曲线」卡当前完全被动：后端 AgentLoop **从不 emit `chart` 事件**（`projector.py` 的 `_on_chart` 与前端 SSE `chart` handler 早已就绪，但无发射方），曲线卡实际永远为空，只有 `run_backtest` 结果里的标量指标作 fallback。

目标：由 chat agent **主动决定**右侧面板显示什么（图表或 HTML 报告），双显（聊天消息流 + 右侧面板），详见 `docs/design/right-panel-agent-driven.md`（另文）。

### 2. Goal 状态靠 3s 轮询，浪费且陈旧

`useGoalPolling.ts` 在右侧面板打开时每 3s 轮询 `GET /api/goal/status`（每次打开 SQLite 连接做全量查询）。但后端**早已在发 goal SSE 事件**：

- `service.py:765 _maybe_emit_goal_event`：聊天中 `create_goal` / `add_evidence` / `complete_goal` 工具执行后发 `goal_updated` / `goal_evidence_added` / `goal_completed`
- `chat.py:833 _emit_goal_sse_event`：`/goal` 斜杠命令后发同样事件

`useGoalPolling.ts` 注释"后端从不发 goal_* 事件"是**过时信息**。轮询是历史兜底，链路已就绪却没摘掉。

### 3. Goal 变更在聊天消息流中无记录

goal_* 事件只进 `event_log`，但 projector 的 handler 表（`projector.py:197-229`）**没有 goal 事件**——投影时被忽略，messages 表里没有痕迹。聊天记录里只有工具的稀疏返回字段：

| 工具 | 返回字段 | 缺什么 |
|---|---|---|
| `create_goal` | goal_id / goal_status / objective / progress_percent | 整个 criteria 列表 |
| `add_evidence` | evidence_id / auto_attached_to / progress_percent | 逐条 criteria 状态、证据文本 |
| `complete_goal` | goal_id / goal_status / recap | — |

从消息流提取 goal 状态**结构性不可行**（信息不守恒：投影时已丢；分页盲区：create_goal 可能在加载窗口外；非聊天路径：REST/workflow 无投影）。详见本项目讨论记录。

---

## 决策记录

| # | 决策 | 理由 |
|---|---|---|
| D1 | 删除 3s 轮询，goal 状态改由 **SSE 全量快照事件**驱动 | 链路已存在（service/chat 两处 emit + 前端 handler），只差完整性；事件驱动毫秒级、零常规请求 |
| D2 | `_maybe_emit_goal_event` 统一发**一条全量 `goal_updated`**（含 criteria 列表 / evidence_count / progress / recap），删除 `goal_evidence_added` / `goal_completed` 增量事件 | 全量覆盖消除增量漂移；`chat.py` /goal 路径已是全量，对齐之 |
| D3 | REST 写路径（`POST /api/goal/start|evidence|complete`）**补发**同款全量 `goal_updated` | workflow 页 / 脚本变更 goal 也实时；SSE 单一真源 |
| D4 | goal_* 事件**持久化进消息流**（`message_type='goal'` 独立消息，幂等 by event id） | 聊天记录完整可审计；刷新后 loadMessages 可见 |
| D5 | goal 消息**进 LLM 上下文**（agent 始终掌握目标演化），role=**system** + content 前缀自解释 | agent 需要目标状态决策；OpenAI 兼容协议允许 system 中插；项目已有 todo 快照 system 中插先例（`loop.py:1886`）；前缀防歧义 |
| D6 | 证据文本分级：LLM 层（content）截断（默认 100 字，**可配置** `goal_evidence_truncate_chars`），UI/审计层（metadata）完整 | 上下文成本受控（50 条证据 ≈ 15-20K token，占 128K 窗口 ~15%）；审计价值在 UI 层 100% 保留 |
| D7 | UI 显示折叠：变更类型标签 + 关键信息，criteria / 完整证据默认折叠展开 | 消息流不被高频证据刷屏 |
| D8 | 旧事件类型 `goal_evidence_added` / `goal_completed` 与 `updateGoal` 增量逻辑删除 | 单应用部署无外部消费者；dispatcher 对未注册事件静默丢弃（`handlers.ts:56-58`）；全量覆盖兜底重放 |

---

## 消息模型

### goal 消息（messages 表）

| 字段 | 内容 | 消费者 |
|---|---|---|
| `role` | `system` | LLM 上下文 |
| `message_type` | `goal` | 前端渲染分支 |
| `content` | 紧凑文本：`[目标状态] 系统生成的目标快照（非用户输入）。创建目标: "<objective>" \| 进度 45% \| 标准: [c1(covered), c2(in_progress)] \| 最新证据: "<截断文本>"` | LLM 上下文 |
| `metadata`（metadata_json） | 结构化全量：`{change_type, goal_id, objective, status, progress_percent, evidence_count, criteria: [{criterion_id,text,status,evidence_count}], evidence_text(完整), recap, session_id}` | 前端渲染 + 审计 |
| `seq` | 正常参与压缩边界（`compacted_until_seq`） | 旧 goal 消息随压缩自动退出上下文 |

### SSE 事件（`goal_updated`）

```python
{
  "goal_id", "session_id", "goal_status", "objective",
  "progress_percent", "recap",
  "criteria": [{"criterion_id","text","status","evidence_count"}],
  "evidence_count",
  "change_type": "create" | "evidence" | "complete",
  "evidence_text": "<完整文本>",      # 前端实时入流 + projector 持久化
  "evidence_text_llm": "<截断文本>",  # projector 构造 content 用（已截断，免投影器读配置）
}
```

`evidence_text_llm` 由共享 helper 按 `cfg.compact_config.goal_evidence_truncate_chars`（默认 100）截断，projector 纯透传不感知配置。

### 配置项（`~/.quantnodes/llm.json`）

```jsonc
{ "compact": { "goal_evidence_truncate_chars": 100 } }
```

- 加入 `CompactConfig`（`core/agent/compact.py`），沿用 `llm.json` "compact" 段解析（`config.py:418-427`）
- 只影响 LLM 层 `content` 截断；`metadata` 始终完整

---

## 数据流

```
写路径（3 条）                        SSE 事件                DB / UI / LLM
─────────────────                  ──────────────           ─────────────────────
聊天工具 create_goal/           → 共享 helper               → projector._on_goal_updated
add_evidence/complete_goal        build_goal_updated_payload   → goal 消息（幂等）
  (service.py tool_result)              │                        ├─ 前端 SSE 实时入流
/goal 斜杠命令                      → 同 helper                │     （面板 setGoal + 消息 addMessage）
  (chat.py)                            └─ 前端 SSE handler       ├─ 刷新后 loadMessages 加载
REST POST /api/goal/start|               （面板全量 setGoal）    └─ LLM 上下文：history 转换
evidence|complete (goal.py)                    + 消息型 addMessage   → role=system 保留 + 前缀
```

### LLM 上下文（关键机制，已核实）

- agent 每次 run_attempt **从 DB 加载消息**（`service.py:549` `store.get_messages(session_id, limit=100)`）→ `_convert_messages_to_history`
- goal 消息经 history 转换 **goal 分支**：保留 `role=system`（跳过 `role not in ("user","assistant")` 过滤，`service.py:1418`），content 原样
- 上下文自动受控：goal 消息 `seq <= compacted_until_seq` 时被压缩隐藏（`service.py:1407` 过滤条件对 goal 生效）；`limit=100` 兜底
- trim（字符预算 newest→oldest）对 goal 消息无特殊性，旧条目先丢

### 前端双通道

- **SSE 实时**：`goal_updated` 事件 → `metaHandlers.goalUpdated`（面板全量 setGoal，已有）+ 新消息型 handler（`addMessage` 入流，参照 `assistant_message` 模式）
- **刷新恢复**：`loadMessages` 从 DB 加载 goal 消息（projector 已持久化，幂等不重复）
- **启动/切会话**：`loadSessionState` 全量快照恢复（已有，`session.ts:223`）
- **断线**：EventSource `Last-Event-ID` + 后端 `sse_buffer` 重放（已有）
- **轮询删除**：`useGoalPolling.ts` 删除；REST 已补 SSE，无需 focus 兜底（保留 `loadSessionState` 原有调用点）

---

## 改动清单

### 后端

| # | 文件 | 改动 |
|---|---|---|
| B1 | `api/session/event_v2.py` | EventType 加 `GOAL_UPDATED = "goal_updated"` |
| B2 | `core/agent/compact.py` | `CompactConfig` 加 `goal_evidence_truncate_chars: int = 100` |
| B3 | 新 `core/goal/events.py` | `build_goal_updated_payload(session_id, store, change_type, cfg)`：全量快照 + change_type + evidence_text（完整）+ evidence_text_llm（截断） |
| B4 | `api/session/projector.py` | `_on_goal_updated`：幂等创建/更新 goal 消息（by event id，参照 `_on_compact` existing 检查；content=前缀紧凑文本，metadata=结构化全量） |
| B5 | `api/session/service.py` | `_maybe_emit_goal_event` 改用 B3 helper 统一发全量 `goal_updated`（删增量分支）；`_convert_messages_to_history` 加 goal 分支（保留 system role） |
| B6 | `api/routers/chat.py` | `/goal` 命令路径改用 B3 helper（payload 对齐） |
| B7 | `api/routers/goal.py` | REST 三端点（`/start` `/evidence` `/complete`）成功后补发全量 `goal_updated` |

### 前端

| # | 文件 | 改动 |
|---|---|---|
| F1 | `stores/chat.ts` | `MessageType` 加 `'goal'` |
| F2 | `hooks/sse/messageHandlers.ts` | 新 `goalUpdated` 消息型 handler（addMessage 入流） |
| F3 | `hooks/sse/metaHandlers.ts` | `goalUpdated` 保留（面板全量 setGoal）；删 `goalEvidenceAdded` / `goalCompleted` |
| F4 | `hooks/sse/types.ts` / `handlers.ts` | EVENT_TYPES / HANDLERS 注册表删旧两类型 |
| F5 | `stores/goal.ts` / `hooks/useSSE.ts` | 删 `updateGoal` 死代码 |
| F6 | `components/chat/MessageList.tsx` | goal 渲染分支：变更类型标签 + objective/证据前 50 字 + 进度；criteria/完整证据默认折叠展开 |
| F7 | `hooks/useGoalPolling.ts` | 删除；`RightPanel.tsx` 移除调用 |
| F8 | `test/*` | 同步更新（见测试计划） |

### 测试计划

**后端**（pytest）：
- `build_goal_updated_payload`：全量字段、change_type、截断参数化（默认 100 / 配置覆盖）
- projector `_on_goal_updated`：创建、幂等重放、metadata 持久化 round-trip（flush → 重新投影）
- history 转换：goal 消息保留 system role 进 LLM 上下文；compaction 隐藏 seq 边界内 goal 消息
- REST 三端点 emit 断言（payload 全量）
- `_maybe_emit_goal_event`：create/evidence/complete 三种工具结果 → 全量 goal_updated

**前端**（vitest）：
- goal 消息渲染（折叠/展开、变更类型标签）
- SSE goalUpdated 双通道（面板 setGoal + 消息入流）
- `metaHandlers` 删旧两事件后注册表一致性
- `useGoalPolling` 删除后相关测试移除；`goalStore` updateGoal 移除

### 验证

- `pytest` 相关子集 + 全量 compact/chat 测试
- `vitest` + `tsc -b`

---

## 边界与安全

- goal 消息内容由后端构造（projector 从事件 payload 透传），前端仅渲染 metadata，无 XSS 面（React 默认转义；证据文本含用户/LLM 生成内容，一律文本节点渲染）
- goal 消息占 messages 表行数 + `limit=100` 加载名额：高频证据会挤占早期对话——由压缩机制 + trim 兜底；若后续需要可单独提高 limit 或合并消息
- 截断仅影响 LLM 层；`metadata` 完整文本是审计真源
