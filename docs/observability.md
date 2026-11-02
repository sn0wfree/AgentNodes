# 可观测性（Observability）

策略研究平台的可观测性 = **三层信号**：运行时状态（dump）、写入吞吐（metrics）、
卡死防护事件（hangs）。本文档是运维入口：先看"现在卡没卡"，再看"过去 24h 卡了几次"。

## 总览

| 能力 | 入口 | 鉴权 | 何时用 |
|---|---|---|---|
| 进程内状态 dump | `GET /api/study/_internal/dump?session_id=` | X-Admin-Token | 卡死 triage：哪个 study 在跑、跑几轮、心跳多老 |
| 会话写入吞吐 | `GET /api/admin/metrics` | X-Admin-Token | 磁盘/DB 写入是否异常（rate / duration / success） |
| 卡死防护事件报告 | `GET /api/admin/hangs/report` | X-Admin-Token | 过去 24h 各防护层触发频率，阈值调优依据 |
| 卡死防护事件（CLI） | `strategy-research hangs --hours 24` | 本地 | 无 HTTP 时手动补跑日报 |

Admin token：`SR_ADMIN_TOKEN` 环境变量。未设置时 admin 端点返回 `503`。

---

## 1. 进程内状态 dump（A）

**端点**：`GET /api/study/_internal/dump?session_id=<sess>&study_id=<可选>`
（`X-Admin-Token` 头）

返回当前进程（而非 DB 回放）的调度器 + 全部该 session 的 study 状态：

```json
{
  "session_id": "sess-x",
  "db_path": "/home/ll/.quantnodes-research/goals.db",
  "generated_at": "2026-08-14T13:00:00+00:00",
  "watchdog":   { "alive": true, "interval_s": 60, "heartbeat_timeout_s": 3600 },
  "concurrency":{
    "semaphore_limit": 3,
    "queued_study_ids": [], "active_executor_ids": ["st-1"], "active_task_ids": ["st-1"],
    "queued_count": 0, "active_count": 1
  },
  "session_queues": { "sess-x": { "queued_depth": 0, "consumer_alive": true } },
  "studies": [{
    "study_id": "st-1", "objective": "...", "strategy_name": "...",
    "execution_status": "running", "current_round": 3, "max_rounds": 20,
    "heartbeat": "2026-08-14T12:59:30+00:00", "heartbeat_age_s": 30.2,
    "last_error": null,
    "hanging_protection": {
      "is_active_in_scheduler": true,
      "heartbeat_stale": false,
      "watchdog_will_interrupt": false
    }
  }],
  "hanging_signals_in_window": {
    "wallclock_timeout": 0, "log_stall": 0, "no_progress": 0,
    "circuit_breaker_open": 0, "watchdog_interrupt": 0
  }
}
```

**triage 决策表**（卡死排查）：

| 现象 | 判定 | 处置 |
|---|---|---|
| `watchdog.alive=false` | 调度器 watchdog 没起来 | 检查进程启动日志；基本不会发生（lazy 拉起） |
| `watchdog_will_interrupt=true` | 该 study 会在下次 sweep 被强制中断 | 若不想等，手动 cancel；若想救，确认 LLM/回测是否真活着 |
| `heartbeat_age_s` 持续增长 | 轮间无进展 | 查 `study_rounds` 最近完成时间；回测子进程是否 `log_stall` |
| `hanging_signals_in_window.no_progress>0` | agent 连续 N 次相同 tool_call | 查该 study 最近轮 agent 日志，考虑给 agent 换工具/换目标 |
| `concurrency.active_count=0` 但 status=running | 状态与调度器脱钩（DB 残留） | `submit()` 幂等会拒绝；直接 cancel 后重新 start |

`/api/study/_internal/dump` 不检查 session 归属（operator 级），但挂在
`/api/study/_internal/` 前缀下，`AuthMiddleware` 已放行（admin 鉴权）。

## 2. 会话写入吞吐（B）

**端点**：`GET /api/admin/metrics?recent=20`（X-Admin-Token）

包装 `core/session/metrics.py::MetricsLogger`（内存环形 10k + JSONL 落盘）。
返回：

```json
{
  "status": "ok",
  "stats": {
    "total_writes": 4821, "total_messages": 90213,
    "success_rate": 0.998, "avg_rate": 312.4, "max_rate": 2090.1,
    "min_rate": 0.0, "avg_duration": 0.004, "total_duration": 19.1
  },
  "recent": [ { "ts": ..., "count": ..., "duration": ..., "rate": ..., "ok": true } ]
}
```

解读：
- `success_rate` 明显低于 1 → 磁盘写失败（锁/磁盘满），配合 `recent[].ok=false`
- `avg_duration` 漂移 → DB 膨胀/锁竞争；对照 `strategy-research session stats` CLI 双检

**CLI 双胞胎**：`strategy-research session stats`（内存 + JSONL 聚合）。

## 3. 卡死防护事件报告（C）

**事件表**：`goals.db::hanging_events`（5 类事件，`core/study/hanging_events.py`）

| event_type | 触发点 | 阈值 |
|---|---|---|
| `wallclock_timeout` | LLM 流式墙钟超时 | `SR_AGENT_WALLCLOCK_TIMEOUT`（默认 1800s） |
| `log_stall` | 回测/后台子进程日志停滞 | `SR_BACKTEST_STALL_TIMEOUT`（默认 300s） |
| `no_progress` | agent 连续 N 次相同 tool_call | `no_progress_window`（默认 3） |
| `circuit_breaker_open` | 工具熔断器 OPEN | `failure_threshold` / `max_total_failures` |
| `watchdog_interrupt` | scheduler 心跳陈旧强制中断 | `SR_STUDY_HEARTBEAT_TIMEOUT`（默认 3600s） |

**HTTP**：`GET /api/admin/hangs/report?hours=24&limit=50` → `{status, window_hours, report}`，
`report` 含 `total_events / by_type / by_study / recent`。

**CLI**：`strategy-research hangs --hours 24 --limit 50`（本地直连 goals.db）。

**用法**：阈值调优看 `by_type` 分布——`wallclock_timeout` 频繁 → 墙钟太短；
`no_progress` 高 → agent 卡循环而非超时；`watchdog_interrupt` 高 → 心跳间隔/轮时长
不匹配。这是每天（或每批研究后）跑的"卡死日报"。

---

## 4. 结构化日志 + trace_id（D）

### 4.1 启用 JSON 日志

```
SR_LOG_JSON=1   # 启用单行 JSON 日志（默认关闭，输出纯文本）
```

启用后每行日志变为：

```json
{"ts":"13:45:01","level":"INFO","logger":"strategy_research.core.llm.openai_client",
 "trace_id":"a1b2c3d4e5f6","session_id":"sess-1","study_id":"st-9","round_num":3,
 "msg":"stream retryable status 429 (attempt 1/3)"}
```

`trace_id`/`session_id`/`study_id`/`round_num` 为空时自动省略，减少噪声。

### 4.2 trace_id 传播链

四个 `ContextVar` 在 asyncio task 图中自动传播（`asyncio.create_task`
拷贝当前 context）：

```
HTTP /api/chat/send_async
  └─ SessionService._run_attempt          ← bind: trace_id=attempt_id, session_id
       └─ AgentLoop._run_loop_core        ← fallback bind: session_id (if not set)
            └─ client.astream / achat     ← 日志含 trace_id + session_id
            └─ StudyRunner._run_one_round ← bind: study_id, round_num
                 └─ client.stream / chat  ← 日志含 study_id + round_num
```

**绑定点**（代码位置）：

| 层 | 文件:函数 | 绑定的字段 |
|---|---|---|
| 会话请求 | `api/session/service.py::_run_attempt` | `trace_id`(=attempt_id), `session_id` |
| Agent 循环 | `core/agent/loop.py::_run_loop_core` | `trace_id`(fallback), `session_id`(fallback) |
| Study 轮次 | `core/study/runner.py::_run_one_round` | `study_id`, `round_num` |

`_run_loop_core` 仅在 ContextVar 未被上层设置时才 fallback（`if not _trace_id.get()`），
所以 study runner 内的 agent 调用会继承 `_run_attempt` 设的 trace_id +
`_run_one_round` 设的 study_id/round_num。

### 4.3 grep / jq 示例

```bash
# 所有 LLM 墙钟超时事件（JSON 日志）
journalctl -u strategy-research | jq 'select(.msg | contains("wall-clock"))'

# 某次 attempt 的完整调用链
journalctl -u strategy-research | jq 'select(.trace_id=="a1b2c3d4e5f6")'

# 某 study 的所有轮次日志
journalctl -u strategy-research | jq 'select(.study_id=="st-9")'
```

### 4.4 TraceFilter + JsonFormatter 内部

- `core/observability/trace.py` -- 全部实现（4 个 ContextVar + `bind_trace`
  context manager + `TraceFilter` + `JsonFormatter` + `setup_trace_logging`）
- `TraceFilter` 挂在 root logger 上，每条 LogRecord 经过时注入
  `record.trace_id/session_id/study_id/round_num`
- `setup_trace_logging()` 幂等（filter 只挂一次），`SR_LOG_JSON=1` 时
  把所有 root handler 的 formatter 换成 `JsonFormatter`

### 4.5 已修 bug

`_check_wallclock` 之前读 `getattr(self.config, "session_id", None)`，
但 `LLMConfig` 无 `session_id` 字段 -> 恒 `None`。改为从 ContextVar
`_session_id.get()` 读取，wallclock 超时事件现在能正确关联到 session。

---

## 5. 埋点与失败容忍

- 所有事件写入 `best-effort`：写失败吞异常，**不影响主路径**（LLM 流、agent 循环、
  调度器）。
- `hanging_events` 与 `studies` 同文件（goals.db），`CREATE TABLE IF NOT EXISTS`
  幂等，无迁移。
- 事件表会无限增长（每事件一行）。建议定期 `DELETE FROM hanging_events
  WHERE created_at < strftime('%s','now')-30*24*3600`（30 天），或跑
  `strategy-research hangs` 后手工清。

## 6. 页面可观测性（Phase 3/4）

### 6.1 轮次详情端点（Phase 3）

| 端点 | 说明 |
|---|---|
| `GET /api/study/{id}/rounds/{n}/artifacts` | 轮次产物文件列表（round 根 + run_* 目录，path/size/mtime 按时间倒序） |
| `GET /api/study/{id}/rounds/{n}/manifest` | manifest.json（hypothesis / strategy_changes / metrics / verdict / next） |
| `GET /api/study/{id}/rounds/{n}/diff?against={m}` | 两轮 strategy.py unified diff；`against=0` 对 baseline；返回 add/del/context 行 + 统计 |
| `POST /api/study/{id}/rounds/{n}/adopt` | 非破坏性采用：复制该轮 strategy.py 到 `ws/study/{id}/baseline/`（下一轮继承源），不动共享 `strategies/<name>/baseline` |

### 6.2 事件与 trace 接 UI（Phase 4）

#### 6.2.1 Trace 单一事实源（A1–A3）

`event_log` 是 agent 轨迹（Trajectory View）的单一事实源。AgentLoop 每个
LLM 调用发 `llm_request`（大字段 `system_prompt`/`tools_schema` 侧车 offload
到 `<event-db>/trace-blobs/`），并把生命周期事件（`loop_start`/`loop_end`/
`loop_final`/`iter_start`/`llm_response`/`compression`/`tool_error`）也写入
event_log。`GET /api/chat/session/{id}/trace` 由 `TraceProjection` 从
event_log 投影出总结级事件并还原 offload 大字段；`trace.jsonl` 仅作
A1 之前旧会话的后向兼容回退。

- 投影默认返回总结级词汇（不含 `text_delta`/`thinking_delta` 等高频原始流）；
  传 `types=a,b` 白名单可过滤。
- 实现：`api/session/trace_projection.py`（投影）、`api/session/service.py`
  `_LoopEventForwarder._offload_large_fields`（offload）、
  `core/agent/loop.py::_trace_and_emit`（双写）。
- 前端 `TraceViewer`（`webui/.../chat/TraceViewer.tsx`）消费该端点渲染时间线。

- `GET /api/study/{id}/hanging_events?hours=24&limit=20`：该 study 近 N 小时
  卡死事件（`by_type` 计数 + `recent` 列表，含 `created_at_iso`）。
- 所有 `study_*` SSE 事件（`study_started` / `study_round` / `study_failed` …）
  均带 `trace_id` / `study_id` / `round_num`。`trace_id` 每 study 一次生成、
  跨轮稳定（`AutoresearchRunner._trace_id`），页面错误条可一键复制去日志查询：
  `journalctl -u strategy-research | jq 'select(.trace_id=="...")'`。

### 6.3 状态机 v2（Phase 5）

- `GET /api/study/{id}/available_actions`：返回当前状态允许的操作
  （name / label / destructive）—— **前端按钮由后端驱动**，不再硬编码。
- `POST /api/study/{id}/actions/{name}`：统一操作入口
  （pause / resume / resume_interrupted / cancel，body 可带 `reason`）；
  状态不允许时返回 409。
- `POST /api/study/{id}/rounds/{n}/redo`：丢弃该轮（DB 行 + state.json
  回退 + round 目录删除）并重新排队从 `round n-1` 重跑；running 状态拒绝（409）。
- 操作矩阵定义在 `core/study/models.py::ACTION_MATRIX`：QUEUED→{cancel}，
  RUNNING→{pause,cancel}，PAUSED→{resume,cancel}，INTERRUPTED→{resume_interrupted}，
  MONITORING→{pause,cancel}；终态（complete/cancelled/error/budget_limited/
  early_stopped/needs_refresh）无操作。

## 7. 相关代码

| 组件 | 文件 |
|---|---|
| dump 端点 + 组装 | `src/strategy_research/api/routers/study.py`（`_study_dump`） |
| scheduler dump helper | `src/strategy_research/core/study/scheduler.py` |
| admin metrics | `src/strategy_research/api/routers/admin.py` |
| 事件表 + 报告 | `src/strategy_research/core/study/hanging_events.py` |
| 事件埋点 | `core/llm/openai_client.py`、`core/study/scheduler.py`、`core/agent/loop.py`、`core/agent/circuit_breaker.py` |
| trace context + JSON | `core/observability/trace.py` |
| agent 事件词汇表 | `core/observability/event_vocab.py`（SUMMARY_EVENT_TYPES） |
| trace 绑定点 | `api/session/service.py`、`core/agent/loop.py`、`core/study/runner.py` |
| 轮次详情端点 | `api/routers/study.py`（artifacts/manifest/diff/adopt/redo/hanging_events/actions） |
| 状态机矩阵 | `core/study/models.py::ACTION_MATRIX` + `allowed_actions()` |
| 测试 | `tests/test_study_dump.py`、`tests/test_admin_metrics.py`、`tests/test_hanging_events.py`、`tests/test_trace_context.py`、`tests/test_study_round_detail.py`、`tests/test_study_actions.py`、`tests/test_agent_event_wiring.py` |

---

## 8. study/goal 的 agent 事件（2026-11-02 接线）

设计：`docs/study-goal-agent-events-design-20261102.md`。

**此前**：agent 级事件（`llm_request` / `iter_start` / `tool_call` / …）只在
chat 路径可见（`on_event=forwarder` → event_log）。study 的 9 个研究 agent
与 goal/swarm 的全部 agent 执行轨迹**被丢弃**——`build_agent_loop` 不接受
`on_event`，`SwarmRuntime` 调 executor 时也没传。研究是核心业务，其执行
轨迹不可见是最大的观测盲区。

**现在**：两条链都接通了，且共用同一张白名单。

```
study 链（进 event_log，与 study_* 同 aggregate 可联查）
  phase_engine → run_*_phase(on_event=runner._make_agent_on_event(session))
    → _make_spawn_fn（注入 data["agent"]=名字）→ spawn_agent
    → run_agent_via_llm → build_agent_loop → AgentLoop(on_event=…)
  sink 三件事：白名单过滤 / llm_request 大字段剥离 / 复用 _emit（自动带
  ContextVar 的 trace_id/study_id/round_num；emitter 为 NullEmitter 时零成本）

goal 链（进 WorkflowEventBus 观察者总线）
  SwarmRuntime(on_event=goal._agent_event_bridge())
    → _wrap_agent_on_event（白名单 + 注 agent 名）→ executor.execute(on_event=…)
  与 agent_start/agent_complete 层级事件并存；观察者按类型自取。
```

### 8.1 白名单：`SUMMARY_EVENT_TYPES`

`AgentLoop._emit` 对每个流式 chunk 都发 `text_delta`/`thinking_delta`。
chat 全量转发是 SSE 语义；study 一轮 9 agent × 每 LLM 调用几百 chunk，
不过滤就是每轮几千行 event_log。白名单（`core/observability/event_vocab.py`）
只放总结级类型，与 `TraceProjection` 的投影口径一致。

### 8.2 大字段剥离

`llm_request` 携带 `system_prompt` / `tools_schema` 全文（各 1–3 万字符）。
chat 路径经 `_LoopEventForwarder` offload 到 trace-blobs；study 的 sink 直发
EventStore，必须先剥——保留 `system_prompt_hash` / `tools_hash` /
`system_prompt_len` 指纹（定位「prompt 变没变」足够，体积差三个量级）。

### 8.3 CLI/MCP 的 SR_LOG_JSON 开关

`setup_trace_logging()` 此前唯一生产调用点是 `api/app.py`——CLI/TUI/MCP
进程既无 TraceFilter 也无 JsonFormatter，trace_id 关联只在 Web 生效。
现在：

| 入口 | 行为 |
|---|---|
| Web | 不变（`create_app` 装配，`SR_LOG_JSON` 可选） |
| CLI/TUI | `SR_LOG_JSON=1` 时才建 stderr handler + JSON 格式 + trace 过滤；**默认零改动**（TUI 是全屏界面，无条件挂 handler 会刷穿渲染） |
| MCP stdio | `serve_stdio` 总是装配 `basicConfig` + TraceFilter（此前进程无 handler，INFO 日志全部不可见）；日志落 stderr，stdout 只走协议帧，`SR_LOG_JSON=1` 同样生效 |

CLI 日志走 stderr（`logging.basicConfig` 默认），不会污染 TUI 的 stdout 渲染。

---

## 附录：事件源架构（P0-1）

`event_log` 表是事件源（event-sourced audit log）：每一行 append-only，
schema 固定（`core/storage/event_schema.py::EVENT_LOG_DDL`），含主键
`id`、聚合根 `aggregate_id`、单调 `seq`、事件 `type`、`data_json`、
`time_created`、`parent_event_id`、`branch_id`。`UNIQUE (aggregate_id,
branch_id, seq)` 保证分支内单调。

读路径：

- `GET /session/{id}/trace` → `TraceProjection.project()` → `EventStore.replay(types=…, branch_id=…, limit=…)`。
  SQL 层 WHERE `type IN (...)` 过滤，~95% 反序列化被跳过。
- `Projector.project()` 优先从 `snapshots` 表加载最近
  `ProjectedSession`（flush 每 200 事件写一次），然后 replay delta；
  5000 事件 session 冷启 O(N) → O(delta)。

写路径：

- `EventStore.emit()` 持久化 + cache + SSE push + Projector flush。
  `flush()` 在同事务内 UPSERT `messages` / `message_parts` + 写 snapshot。
- 大字段（`system_prompt` / `tools_schema` / `content`）offload 到
  `<event-db>/trace-blobs/`，引用记入 `blob_refs(blob_path, ref_count,
  first_seen, last_access)`。`scripts/cleanup_blobs.py` 按 TTL=365 天
  (`SR_BLOB_TTL_DAYS`) 删除非活跃 blob，写 `blob-cleanup-audit.log`。

运维：

- `scripts/migrate_event_log_p0_1_a4.py [--dry-run]` — 旧 UNIQUE
  `(aggregate_id, seq)` → `(aggregate_id, branch_id, seq)` 重建迁移。
- `scripts/cleanup_blobs.py [--apply] [--ttl-days N]` — blob 清理。
- `EventStore.health_report()` 暴露 `cache_hit_rate` 供 LRU 容量调优。

详情见 `docs/p0-1-event-sourcing-design.md`（母文档）与各 phase
子文档（A1-A4, B1-B4, C1/C3, D）。

---

## 附录：Capability Seam（P0-2）

数据源 / 回测 / 沙箱三层从"硬编码实现"转为 Protocol + Provider：

- **`DataStore`** (`core/storage/data_store.py`) — 鸭子类型 facade，
  覆盖 load_ohlcv_data / 因子注册表 / 回测结果 / 权重 NAV 历史 / 校验
  缓存 / 导入元数据 / 数据指纹。`DuckDBDataStore` 是默认实现
  （薄包装 `core/db.py` 函数），registry 在 `data_store_registry.py`。
  工具 / 服务通过 `ToolContext.data_store` 注入，或调用
  `from strategy_research.core.storage import get_store`。
- **`BacktestResult`** (`core/backtest_models.py`) — 5 字段权威定义
  （含 `factor_failures`）。`utils/backtest_engine` 与
  `utils/strategy_engine` re-export，import 路径不变。
- **`ExecutionSandbox`** (`core/agent/sandbox/protocol.py`) —
  `validate_source` + `resolve_write` + `resolve_read` 是当前能力；
  `execute_strategy` / `allow_network` / `get_resource_usage` 是未来
  runtime hooks（v0.1 raise `NotImplementedError`）。`StaticSandbox`
  是默认实现（包装 `validate_python_source` + `PathWhitelist`）。
- **`ToolContext` DI** (`core/agent/tools_capability.py`) —
  `AgentLoop._build_data_store` / `_build_sandbox` 自动注入默认值；
  工具通过 `get_data_store(ctx)` / `get_sandbox(ctx)` helper 消费，
  seam 缺失时抛 `ToolCapabilityError`。

新增/迁移调用方：

- `DuckDBDataStore` 包装所有 `core.db` 函数 — v0.1 不强制迁移调用方
- `BacktestResult` 双类型 alias — 现有代码零改动
- `ExecutionSandbox` legacy `sandbox.py` 移到 `sandbox/legacy.py` —
  `__init__.py` re-export 公开名字保持兼容

详情见 `docs/p0-2-capability-seams.md`（母文档）与各 phase
子文档（A/B/C/D）。

---

## 附录：BacktestEngine Protocol（P0-3）

P0-2.B 统一了 `BacktestResult` dataclass，P0-3 把两条回测入口折叠成单一
Protocol：

- **`Strategy`** (`core/backtest_engine/protocol.py`) — 单方法 `compute_weights(date, price_panel, nav_history) -> dict[str, float]`。YAML / Factor 路径已实现此签名；Callback 路径通过 adapter 把五步法流水线折成同一个调用。
- **`BacktestEngine`** — `run(strategy, price_panel, *, config=None) -> BacktestResult`，可选 `config` 参数携带引擎特有的 kwargs（rebal_freq / min_history / cost / BacktestConfig）。
- **`StrategyEngineAdapter`** — 包装 `utils.strategy_engine.StrategyEngine`，YAML 路径 1:1。
- **`CallbackEngineAdapter`** — 包装 `utils.backtest_engine.run_backtest`，五步法（compute_signals / select_assets / compute_weights / apply_risk / post_weights）在内部由一个 `_CallbackStrategyAdapter` 驱动。
- **Registry** (`core/backtest_engine/factory.py`) — `get_engine("strategy" | "callback")`；默认是 `strategy`。

迁移路径：

- 现有调用方继续使用 `utils.strategy_engine.StrategyEngine.run` / `utils.backtest_engine.run_backtest` — 不强制迁移
- 新代码统一通过 `core.backtest_engine` import
- 后续 P0-3+ 可加入 `BaseEngineAdapter` 把 `engine/runner.py` 的 bar-by-bar 路径也接进来

详情见 `docs/p0-3-backtest-engine-protocol.md`。

---

## 附录：LoopStrategy 抽象（P1-1）

`AgentLoop._run_loop_core` 中的硬编码决策点被抽出为 Step Protocol，
由 `LoopStrategy` 组合实现不同循环策略：

- **`LoopContext`** (`core/agent/strategy/loop_context.py`) — Step 间共享的 dataclass（task, messages, iteration, recent_hashes, should_stop, metadata）。
- **9 个 Step Protocol** (`protocol.py`) — PreRunStep / LLMCallStep / CompactionStep / StopStep / ContinuationStep / ProgressStep / ResilienceStep / ToolExecutionStep / FinalizationStep，均 `runtime_checkable`。
- **`LoopStrategy`** + **`LoopConfig`** (`loop_strategy.py`) — 9 个 step 的组合容器；`should_continue` 读 `ctx.should_stop`。
- **`StrategyFactory`** (`factory.py`) — 默认注册 `"react"`；`create_strategy(name=None, config=None)` 入口；`CustomStrategy` 基类允许覆盖特定 step。
- 默认 Step 实现 (`steps/`) — 每个 step 是 v0.1 stub，Progress 和 Resilience 已实现真实逻辑；其他 step 等待 L7 迁移 AgentLoop 时填实现。

v0.1 状态：**基础设施已落地**（types + factory + no-op stubs）；
AgentLoop 实际迁移留作后续迭代。现有 260 个 AgentLoop / EventStore /
tools / bus 测试全绿，证明引入的抽象层未破坏现有行为。

详情见 `docs/p1-1-loop-strategy-abstraction.md`。

---

## 附录：三策略实现（P1-2/3/4）

在 P1-1 `LoopStrategy` 基础设施之上新增三个一策略子类（每个一策略 = 一个 `LoopStrategy` 子类 + 必要时重写 Step）：

| 策略 | 工厂 | 设计重点 | 关键参数 |
|------|------|----------|----------|
| **`ExplorerStrategy`** | `ExplorerStrategyFactory.create()` | 高迭代、宽松进展 | `max_iterations=50, no_progress_window=5` |
| **`ValidatorStrategy`** | `ValidatorStrategyFactory.create()` | 低迭代、严格进展、最终化校验 | `max_iterations=5, no_progress_window=2`, `finalization=ClaimValidationFinalizationStep` |
| **`MinimalStrategy`** | `MinimalStrategyFactory.create()` | 单次 LLM、不调用工具 | `max_iterations=1`, `tool_execution=NoOpToolExecutionStep` |

自定义 Step helper（`core/agent/strategy/custom_steps.py`）：

- **`ClaimValidationFinalizationStep`** — 设置 `ctx.metadata["claim_validation_ran"] = True`，让下游消费者知道策略触发了 claim 校验（真实 call 留给 L7 AgentLoop 迁移时接入）。
- **`NoOpToolExecutionStep`** — context 透传；若 assistant 请求了 tool calls，记录 `ctx.metadata["tool_execution_skipped"] = True`。

注册表：`StrategyFactory.available()` 现在返回 `["react", "explorer", "validator", "minimal"]`，`create_strategy(name=None)` 默认 `react`。注册发生在 `__init__.py`（而非 `factory.py`）以避开循环导入。

详情见 `docs/p1-2-3-4-strategies.md`。

---

## 附录：Profile / LoopStrategy 接入（P1-5）

`Profile`（YAML / dict）通过 `loop_strategy` 字段指向 LoopStrategy：

- **`resolve_loop_strategy()`** (`core/agent/strategy/profile_resolver.py`) — 纯函数，4 种 spec：
  - `None` → 默认 `"react"`
  - `str` (`"explorer"`) → `create_strategy(name)`
  - `dict` (`{"name": "validator", "config": {...}}`) → name + LoopConfig
  - `LoopStrategy` 实例 → 直接透传
  - 非法类型 → `ValueError`；未知 name → `KeyError`

- **AgentLoop 接入** (`core/agent/loop.py`) — 新增 `strategy=` 构造参数：
  - 解析后存到 `self._strategy`
  - `AgentLoop.get_strategy()` 暴露（供 L7 实际驱动）
  - `_run_loop_core` v0.1 暂不读取（行为等价默认 ReAct）

P1-6（`_run_loop_core` 实际驱动 strategy）延期到 L7：
 涉及 60+ 行 for 循环的拆解，独立 PR 更安全。

详情见 `docs/p1-5-6-profile-and-migration.md`。

---

## 附录：AgentLoop → LoopStrategy 接线（L7）

L7 v0.1 把 `AgentLoop._run_loop_core` 接入 `LoopStrategy` step 链，**不破坏** 240+ 现有测试：

- **`_inject_agent_loop(strategy, loop)`**（`core/agent/loop.py`）— 遍历 9 个 step slot，调用支持 `bind_agent_loop` 的 step 实现。
- **`_make_strategy_ctx(loop, messages, response, result, iteration)`** — 构建瞬时 `LoopContext`（让 strategy step 在不修改 legacy 状态的前提下读取）。
- **`DefaultPreRunStep`** + **`DefaultLLMCallStep`** — 真实实现，`execute` 直接调 AgentLoop 的 `_prepare_run` / `_get_response` / `_append_assistant_msg`。其他 step 仍是 v0.1 no-op stub。
- **`LoopContext`** 新增 `result` / `hook_ctx` 可选字段（向后兼容）。
- **`AgentLoop._run_loop_core`** 在两个关键决策点（text-only response / no-tool-call）**先**咨询 `self._strategy.continuation` / `self._strategy.stop`，再走 legacy 路径。Default 是 no-op，Custom 返回 `should_stop=True` 立即短路。

行为保证：默认 ReAct 行为 100% 等价；240+ 现有 AgentLoop 测试全绿。

完整 `_run_loop_core` 重写（60+ 行 for 循环拆为 step chain）留作 v0.2。详情见 `docs/l7-agent-loop-migration.md`。

---

## 附录：L7 v0.2 — 循环决策点迁移（4 个）

`AgentLoop._run_loop_core` 的 4 个控制流决策点全部委托给 `LoopStrategy` 的 Step：

| 决策点 | 委托 | 副作用保留处 |
|--------|------|--------------|
| 停止判定 | `strategy.stop.evaluate` + `strategy.continuation.evaluate` | AgentLoop `_handle_stop` / `_check_goal_continuation` |
| 无进展检测 | `strategy.progress.record_hash` + `is_no_progress` | AgentLoop `_check_no_progress`（`hashes_pre_recorded=True` 防双份） |
| 断路器 | `strategy.resilience.is_open` | AgentLoop `_breaker_open_messages` |
| 迭代上限 | `strategy.config.max_iterations`（显式策略时） | 构造参数 `self.max_iterations` 兜底 |

框架决策：

- **`_call_step()`** — step 统一 try/except 隔离，失败 → `ctx.should_stop = True`
- **`_make_strategy_ctx(hook_ctx=)`** — 瞬时 LoopContext 透传 AgentHookContext 实例（共享同一对象）
- **`DefaultProgressStep` / `DefaultResilienceStep`** — 新增 `bind_agent_loop`，复用 AgentLoop 状态（`_recent_hashes` / `_circuit_breaker`）

Hook 触发迁移（step 自触发 `_afire_hooks`）留待 v0.3。详情见 `docs/l7-v0.2-decision-migration.md`。

---

## 附录：L7 v0.3 — ToolExecutionStep 自触发工具 Hook

首个 "Step 自触发 Hook" 模式落地（框架决策 #1）：

- **`DefaultToolExecutionStep`**（`core/agent/strategy/steps/tool_execution.py`）真实执行工具：
  - 触发 `before_execute_tools`（dispatch 前）
  - 每工具结果后触发 `on_tool_error`（error）或 `after_tool_executed`（成功），顺序与 legacy `_fire_tool_result_hooks` 一致
  - hashes + 结果存入 `ctx.metadata`（`tool_hashes` / `tool_result_msgs`）
- **`_run_loop_core`** 的工具执行块收敛为单次 `_call_step(self._strategy.tool_execution, tool_ctx, async_mode)`；骨架移除 `before_execute_tools` / `_fire_tool_result_hooks` 调用

**推迟到 v0.4**：
- `LLMCallStep` 的 `before_iteration` + `on_error`（需 LLM 调用执行迁移）
- `FinalizationStep` 的 `after_run`（`after_run` 有 2 处调用点，no-progress 早返回绕过 FinalizationStep）

详情见 `docs/l7-v0.3-hook-migration.md`。

---

## 附录：L7 v0.4 — LLM + 终化执行迁移

`LLMCallStep` 与 `FinalizationStep` 的执行迁入 Step（与 v0.3 的 ToolExecutionStep 同模式）：

- **`DefaultLLMCallStep`**（`core/agent/strategy/steps/llm_call.py`）：
  - `execute` 从 sync def 改为 `async def`，await `_get_response`
  - `before_iteration` 在 LLM 调用前触发；`on_error` 在 `_handle_llm_error` 后触发
  - 填充 `ctx.response` / `response_was_tool_call` / `response_content` + append assistant msg

- **`DefaultFinalizationStep`**（`core/agent/strategy/steps/finalization.py`）：
  - 调用 `_finalize_metrics(result, messages, t0)` + `_run_claim_validation(result, messages)`
  - `after_run` hook 在此处触发（正常结束路径）

- **`_run_loop_core` 骨架收敛**：
  - `before_iteration` + `on_error` 从骨架删除（LLMCallStep 自触发）
  - 正常结束 `after_run` + metrics/claim 迁入 FinalizationStep
  - no_progress 早返回路径的 `after_run` 保留在骨架（不经过 FinalizationStep）
  - `response = None` 初始化避免 `UnboundLocalError`

CompactionStep 仍是 no-op stub（其内部调用 `_amaybe_compact` 是压缩引擎，与 LLM/工具执行同属"执行迁移"但压缩逻辑有独立 O(N) token 估算，留作后续单独处理）。

详情见 `docs/l7-v0.4-exec-migration.md`。
