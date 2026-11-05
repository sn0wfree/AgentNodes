# 当前架构 — 2026-10-08（重构后）

> ⚠ **历史文档（已过时）**：描述 v3 五层架构。当前架构见 `architecture-diagram.html`（v6.2）。本文保留以记录演进。

> 分层视图见 `architecture-framework.svg`（六层结构仍准）；**本文档是重构后的细节与契约面**——SVG 管"有什么"，本文档管"怎么连"。
> 对照差异见 §7。扩展点细则见 `extension-points.md`（分工与红线）。

## 1. 六层（与 SVG 对应）

```
接入   CLI/TUI · FastAPI（20 routers）· WebUI · MCP
编排   SessionService · StudyRunner · AgentLoop
引擎   phase/langgraph 查表分派 · goal · workflow · swarm
能力   LLM（7+1 adapters）· 16 toolsets · memory · hooks · skills
数据   DuckDB · workspace FS · event_store · checkpoint
横切   evals（70 golden）· observability · tests（12,287）· CI
```

## 2. AgentLoop 运行时（core/agent）

**组合结构**：`AgentLoop` = 6 个 mixin（W5 拆分）：

| Mixin | 职责 |
|---|---|
| `loop_stream` | 流式 LLM（`_astream_chat`/`_stream_chat`）、tool_call 累积 |
| `loop_telemetry` | hooks 触发（`_fire`/`_afire_hooks`，11 生命周期点）、trace |
| `loop_progress` | 进度回调 |
| `loop_lifecycle` | run 结束、claim validation、metrics |
| `loop_tools` | 工具执行/HeartbeatTimer |
| `loop_compaction` | 上下文压缩 |

主循环（`_run_loop_core`）与 **strategy steps**（9 个：pre_run/llm_call/tool_execution/compaction/resilience/continuation/stop/progress/finalization）构成编排层——step 是**唯一**可改控制流的扩展点（红线见 extension-points）。

**对称契约**（Step 1/2 引入，phase_engine 同款思想）：
- 输入面：`BranchContext`（preamble 产出）
- 输出面：`RoundOutcome`（分支产出，9 必填字段）
- 观察面：hooks（11 点，只读）；注入面：injectors（order 0/≥100，只加数据）

## 3. Study 执行链（core/study）—— 本次重构最深的一条

```
StudyRunner._run_loop（114 行，C901 12）
  ├─ _top_control_check     → 'cancelled' | 'continue' | 'resumed'（cancel/pause/ARCHIVED 保留）
  ├─ 轮准备（objective flush / directives）
  ├─ _run_one_round → run_round_phases（184 行，C901 8）
  │     ├─ _prepare_round_preamble → (BranchContext, state, inherited_from)
  │     ├─ _resolve_engine（dag/env 已真删：值透传，dispatcher fallback → phases + warning）
  │     ├─ _ENGINE_BRANCHES[engine](ctx)
  │     │     ├─ _run_langgraph_branch → RoundOutcome | dict（HITL pause/abort 早退）
  │     │     └─ _run_phases_branch     → RoundOutcome | dict（novelty 拒绝早退）
  │     └─ shared finalization（只消费 outcome.*）
  ├─ _handle_hitl_pause（142 行：M2 时间 bank / M1 interrupt expire / H2 无幽灵轮 / H1 e2 重算）
  └─ _finalize_round（budget/heartbeat/3×SSE/stop/max/early-stop/prev_passed）
```

**语义红线**（搬移逐条保留）：ARCHIVED 不被 CANCELLED 覆盖、HITL 等待不计预算、interrupt 必 expire、resume 后重算 e2、early-stop 仅在 max_rounds 下生效。

## 4. 数据面

| 存储 | 用途 | 生命周期 |
|---|---|---|
| DuckDB（`core/db.py`） | 回测结果、策略数据 | workspace 局部 |
| workspace FS | strategy.py / results.tsv / round 目录 / knowledge.md | study 作用域 |
| `event_store`（SQLite） | session 事件日志（emit：trace 注入→持久化→SSE→projector flush，三助手各 ≤10 分支） | Projector.close() 收尾 |
| checkpoint（langgraph SqliteSaver） | HITL resume | study 作用域 |
| goal store（SQLite） | 目标/证据/journal（两阶段：capture→backfill） | goal 作用域 |

## 5. API 面（本次清理后）

- **已删**：`routers/memory.py`（B12）、`routers/strategy.py`（B11）、goal `market` 字段（B8）
- **表驱动**：`study_dispatch_action` = 解析/校验 + `StudyAction → _act_*` handler 表（7 handlers，409/404/400 语义逐 handler 原样）
- spec 同步纪律：路由/字段删除必须同步 `openapi.json` + `types.gen.ts`（第四轮教训）

## 6. 质量面（harness）

```
本地 pre-commit（E9/F821，staged）
    ↓
CI tests.yml：lint（ruff src=0）+ 12 分片
    ├─ PR：-m "not llm and not e2e and not slow"（9,278 tests，61s）
    └─ push main：全量 12,287
Nightly：真 LLM eval + mean/pass 跟幅门禁（exit 1）
```

flaky 治理：根因清零（连接泄漏 + 微秒 tiebreak），`--reruns` 仅兜底；A/B 归因法入方法论。

## 7. 与本次系列前的架构差异

| 面 | 前 | 后 |
|---|---|---|
| study 分派 | `run_round_phases` 内联 if/else + 局部变量 shim | 查表 + `RoundOutcome`/`BranchContext` 双契约 |
| `_run_loop` | 329 行单函数（C901 32） | 114 行 + 3 个语义保留的 helper |
| dag/env | 静默 remap 假选项 | **已删**（透传 + dispatcher fallback） |
| hooks | composite + unified + adapter | **composite/context/utils**（unified/adapter 已删） |
| LLM 配置 | builder.py + config.load 双轨 | **config.load 单轨**（builder 已删） |
| 消息投影 | to_llm_message 模块 + inline 双轨 | **inline 单轨**（模块已删） |
| memory API | `/api/memory/search` router | **已删**（core/memory_manager 仍在——记忆是能力层不是 API） |
| 进度 | progress.py 双机制（heartbeat + structured） | **heartbeat 单机制**（structured 已删） |

## 8. 阅读顺序（新贡献者）

1. `architecture-framework.svg`（六层）→ 本文档（契约与连接）
2. `extension-points.md`（放哪里）
3. `code-map.md`（文件级地图）→ `INDEX.md`（文档导航）
4. `step1/2/3-*-design.md`（重构的 why——契约与红线的来源）
