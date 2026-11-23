# P1-4 Trace 完整视图：字段映射与实施规划

> 日期：2026-11-23
> 性质：规划（P1-4 起步）；只输出字段映射与实施顺序，不含代码改动。
> 依据：[U-A 执行计划](./ui-ua-frontend-execution-plan-20261122.md) §6 P1-4、§9 B2、
> [侧边栏业界调研](./ux-side-panel-industry-survey-20261123.md) P5/P10、
> 示意 `tmp/mockup/agentnodes-ui-mockup.html` Trace 视图。

## 1. 真实契约（已核实）

**端点**：`GET /api/chat/session/{session_id}/trace?limit=1..500&types=a,b`
（`routers/chat.py`）— 真身是 `kernel/session/trace_projection.py::TraceProjection` 从 event_log 投影；
`trace.jsonl` 仅作 A1 之前会话的回退。已由 `enforce_session_owned` 钉住归属（404/403）。

**响应**：`{ events: [...], total: n, session_id, error? }`

**TraceEvent 字段（前端 `TraceViewer.tsx` 已消费）**：

| 字段 | 含义 | 现状 |
|---|---|---|
| `type` | 事件类型（llm_request/llm_response/tool_result/tool_error/loop_start/loop_end/iter_start/compression/error/heartbeat/tool_heartbeat） | 真实，11 类着色+图标已实现 |
| `ts` / `time_created` | 时间戳（event_log 投影用 wall-clock 秒） | 真实，双字段兼容 |
| `iteration` | 迭代序号 | 真实 |
| `system_prompt` / `system_prompt_len` | 请求 envelope（大字段 sidecar 重建） | 真实，**敏感**，需脱敏 |
| `tools_schema` / `tools_count` / `history_meta` / `history_count` | 请求 envelope | 真实，**敏感**，需脱敏 |

## 2. 示意字段 → 映射表（P1-4 核心交付）

| 示意字段 | 分类 | 处置 |
|---|---|---|
| 迭代事件时间线（R1–R6 + 时间戳） | ✅ 现有可推导 | `iteration` + `time_created` 排序呈现 |
| LLM / TOOL / RESULT 事件类型与语义色 | ✅ 现有 | 复用 EVENT_COLORS/ICONS |
| 每次事件耗时（如 2.4s / in 8.2K out 1.1K） | ⚠️ 部分可推导 | 相邻事件 `ts` 差可算耗时；token 不在 trace 契约（在 llm_usage/attempt），**标记待接入** |
| 模型名（GLM-5.3-Flash） | ⚠️ 部分可推导 | llm_request envelope 若有 model 字段则可取；无则标记未知 |
| `checkpoint` / `已分叉` 标记 | ❌ 缺失 | 后端无 checkpoint/fork 事件类型（P2-3 才设计），**不做可执行入口**，最多标记待接入 |
| 成本 `$0.31` | ❌ 缺失 | 无价格/计费字段，不造数 |
| 活跃信号源过滤（LLM/工具/注入器/系统） | ⚠️ 可前端实现 | 按 type 分组 + 多选过滤（不依赖新字段） |
| 回合数 `本 run 6` | ✅ 现有 | `total` / loop_start 计数 |

## 3. 实施顺序（P1-4）

1. **字段映射锁定**（本文 §2，需产品确认「可推导/缺失」判定）
2. **以真实 TraceViewer 为基**做时间线/筛选/详情：时间正序可切换、type 多选过滤、点击展开 envelope（脱敏后）
3. **错误矩阵**（§9 B2）：空结果 / 401/403（归属错误）/ 404 / 500 / 损坏事件 / 会话切换竞态 / 取消——失败可见且**不显示旧会话数据**
4. **脱敏**：system_prompt / tools_schema / history_meta 的展示走 redaction，禁止把 prompt/工具参数原样贴进日志或截图
5. **验收**：真实会话 + 认证请求核对时间顺序、筛选、详情、响应式；缺字段显示未知，不补零不造数

## 4. 门禁与不做

- 契约无变更（读现有端点）→ 前端全量 + tsc + lint；若改端点则加后端测试 + batch_gate + 数字同步
- 不展示 checkpoint/fork 可执行入口（P2-3 未批准）
- 不把成本/时长等缺失值补零
- Trace 保持右栏入口（P5/P10：不升一级导航）

## 5. 开放问题（需产品/后端确认）

- llm_request envelope 是否稳定携带 model / token 计数？若否，P1-4 只显示 time + type + 脱敏 envelope
- 时间单位：event_log 投影的 `time_created` 是秒，回退 trace.jsonl 的 `ts` 是否毫秒？统一换算的判据需实测钉住
