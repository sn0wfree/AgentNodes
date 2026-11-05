# 目标代码目录结构 — 2026-10-26

> 性质：重构后 `src/strategy_research/` 的目标布局，**后续每一刀的落位检查单**。
> 三条已确认的落位规则（2026-10-26 用户确认）：
> 1. `core/gateway/` 零 FastAPI import（CI import guard 守，0.5）
> 2. `core/orchestration/` 只放跨界契约，不放实现（controller/engine 各有其家）
> 3. 业务域（study/goal/hypothesis/backtest…）目录不动，只做内部重组

---

## 1. 目标结构

```
src/strategy_research/
├─ core/                          # 平台核心（业务无关）
│  ├─ gateway/                    # ★新建 —— Gateway 本体（A2/A3/C5）
│  │  ├─ __init__.py              #   start/stop 生命周期（挂载式单进程）
│  │  ├─ session_controller.py    #   A2：7 阶段准入 / 6 真值
│  │  ├─ capacity.py              #   A2：容量组闸（chat/study/cron）
│  │  ├─ fencing.py               #   A3：activeRunId 认领 + 提交校验
│  │  └─ scheduler.py             #   C5：唯一调度器（收敛现有 3 处）
│  │
│  ├─ orchestration/              # ✅E1 已建 —— 跨界契约与边界
│  │  ├─ contracts.py             #   ✅ WorkRequest / QueueMode / Receipt
│  │  ├─ mailbox.py               #   ✅ 排队域（lane/steer/cap/deadline）
│  │  ├─ entry_point.py           #   ✅ 协议端点门面
│  │  ├─ config_hub.py            #   ✅ 配置中心
│  │  ├─ budget_gate.py           #   ✅ chat 预算门
│  │  ├─ branch_contracts.py      #   ✅ RoundOutcome/BranchContext 中立协议
│  │  ├─ runtime_hub.py           #   A1：preset 图 + provider/模型解析
│  │  └─ run_context.py           #   A4：预算/事件/取消/追踪 per-run 包
│  │
│  ├─ engine/                     # ★新建 —— 执行面（A1/B1）
│  │  ├─ graph_engine.py          #   GraphEngine（包裹 langgraph，支持环）
│  │  ├─ nodes/                   #   llm / react / cpu / graph
│  │  ├─ presets/                 #   chat / research(T0 冻结) / fanout(T1)
│  │  ├─ executor_bridge.py       #   dispatch/events/cancel/heartbeat
│  │  └─ executor/                #   常驻子进程池 + Job Object + T3 沙箱位
│  │
│  ├─ context/                    # A4 —— ContextHub（独立目录，被所有节点调用）
│  │  ├─ hub.py                   #   collect→arbitrate→render→explain
│  │  └─ providers/               #   goal/todo/memory/hypothesis/knowledge
│  │
│  ├─ events/                     # ✅已有（event_v2.py 保留）
│  ├─ storage/                    # 0.4 —— 数据面（合并进现有目录，不新建平行目录）
│  │  ├─ platform_store.py        #   唯一写入者（不变量 7）
│  │  ├─ session_db.py            #   每 session 自包含库
│  │  ├─ registry.py              #   P-3 工件登记 + goal 摘要索引
│  │  └─ migration.py             #   键控迁移表
│  │
│  ├─ agent/                      # A1 迁移后瘦身（loop.py → engine/nodes/react）
│  ├─ study/                      # C1 拆分 → method/ + engine/（业务保留）
│  ├─ goal/ hypothesis/ alpha_zoo/ backtest_engine/ ...  # 业务域不动
│  └─ registry.py                 # ✅已有 → 扩展为 CapabilityRegistry
│
├─ api/                           # E2 后：路由只做 adapter（构造 WorkRequest）
├─ cli/                           # E3：CLI 作为客户端（HTTP 或进程内 adapter）
├─ mcp/                           # E4：MCP 作为 adapter
└─ ...
tests/
├─ test_entry_contracts*.py · test_config_hub.py · test_budget_gate.py
├─ test_branch_contracts.py · test_orchestration_finishing_extended.py    ✅E1
└─ test_gateway_*.py · test_engine_*.py · test_storage_*.py              后续刀
docs/                              # 架构与决策文档（不随代码结构变动）
```

## 2. 已确认的三个布局决定

| # | 决定 | 理由 |
|---|---|---|
| 1 | **engine 与 orchestration 平级** | engine 是执行面（重），orchestration 是边界契约（轻）；混放会让契约层依赖执行层 |
| 2 | **context/ 独立目录** | ContextHub 被所有节点调用，且是「模型看到什么」的唯一所有者 |
| 3 | **storage/ 合并进现有 core/storage/** | 现有 event_schema.py 已在此，往内加 platform_store/session_db/registry，不新建平行目录 |

## 3. 现有文件迁移映射（替换评估的落位版）

| 现有文件 | 去向 | 刀号 | 处置 |
|---|---|---|---|
| `core/agent/loop.py`（AgentLoop + 7 mixin + 9 step） | `core/engine/nodes/react/` | A1 | 平移为 react 节点内部；7 mixin 部分上交引擎服务 |
| `core/agent/loop_stream.py` | `core/engine/nodes/react/streaming.py` | A1 | 随 loop 平移 |
| `core/agent/loop_compaction.py` | `core/context/` + 节点内 | A1/A4 | diagnose 归 ContextHub、treat 归节点 |
| `core/agent/event_store.py` | `core/storage/platform_store.py` + `core/events/` | 0.4 | 接口保留，存储形态改分库 |
| `core/study/phase_engine.py` | `core/engine/presets/research/` + `core/study/method/` | B1/C1 | 阶段图 → preset 数据；方法论 → method/ |
| `core/swarm/runtime.py` | 删除（控制面部分）+ fanout 解释器 | B1 | **控制代码死亡不搬家** |
| `api/session/service.py` 的 `_session_queues` | `core/orchestration/mailbox.py` | A2/E2 | 队列归一，`_process_session_queue` 拆解 |
| `core/agent/loop.py` 的 `_inbox`（steering） | `mailbox.py` steer 分支 | A4 | 现有 30+ 测试为验收基线 |
| `core/seq_generator.py` | event_log 权威 seq | 0.4 | 兼容层或删除 |
| `core/agent/strategy/budget.py` | 保留，A1 与 budget_gate 分层接线 | A1 | **不删**（有 cost_usd 维度） |
| `core/session/rate_limiter.py` | 保留 | — | 写入限流，与 LLM 限流不同层 |
| `api/sse_buffer.py` + `api/session/bridge_v2.py` | 删除（归一 SSE 单轨） | 0.2 | 先立新后删旧 |
| `api/routers/*` | 改 adapter（构造 WorkRequest） | E2 | 逐 router 切换 |
| `cli/*` | 客户端化 | E3 | 无 daemon 时明确报错 |
| `core/study/scheduler.py` 等 3 处调度 | `core/gateway/scheduler.py` | C5 | 唯一调度器 |

## 4. 落位规则（每刀自检）

1. 新文件必须落在上表位置；跨目录 import 必须符合方向（gateway 零 FastAPI；orchestration 零 engine/agent import）
2. 业务域目录（goals/hypothesis/study/backtest…）**只做内部重组，不搬位置**
3. 删除现有文件必须同时删除对应测试（或迁移测试），不留孤儿测试
4. 每刀合入前跑全量 E1 契约测试（88 条）确保边界不破

## 5. 可复用代码清点（2026-10-26）

### 5.1 各层复用度与来源

| 目标层 | 复用度 | 可复用的现有代码 |
|---|---|---|
| `orchestration/`（E1） | 0%（已新建） | 无（入口层缺失的证明） |
| `gateway/session_controller` | 60% | `api/session/service.py` turn 生命周期 · `web_session_state.py` · `study/runner.py` 状态字段 |
| `gateway/capacity` | 60% | `core/study/scheduler.py` Semaphore · `service.py` 并发限制 → 反转成闸语义 |
| `gateway/fencing` | **0%（全新）** | 无现成实现（问题⑧）；参照 event_store 的 UNIQUE 约束思想 |
| `gateway/scheduler` | 60% | `core/study/scheduler.py` + `api/routers/study.py` + `core/scheduled_research/` 三合一 |
| `engine/nodes/react` | **90%** | `core/agent/loop.py`（~2000 行）+ `loop_stream.py`（262 行）+ `loop_telemetry.py` + `loop_compaction.py` |
| `engine/graph_engine` | 30% | 无包裹层；`core/study/langgraph_engine.py` 是 langgraph 用法最佳参考 |
| `engine/nodes/cpu` + `executor/` | 70% | `core/backtest_engine/` 执行入口（不重写回测）；`core/agent/executor.py` 概念参照 |
| `engine/presets/research` | **85%** | `core/study/phase_engine.py`（1200+ 行阶段函数体）+ `langgraph_engine.py` 两分支合并 |
| `engine/presets/chat` | 80% | `core/agent/strategy/loop_strategy.py` + 3 presets 直接映射 |
| `engine/presets/fanout` | 50% | `core/swarm/` 只用 DAG 调度逻辑，控制面丢弃 |
| `context/hub` | **85%** | `context_builder.py` + `context_injector.py` + `prompt_builder.py`（4 builder 合一）+ `token_utils.py` |
| `context/providers/*` | **95%** | `core/goal/context.py` · `loop_context.py`(todo) · `core/memory/` · `core/hypothesis/` 只读接口 |
| `storage/platform_store` | **80%** | `core/agent/event_store.py`（600+ 行，emit/subscribe/replay 接口全保留）+ `projector.py` 沿用 |
| `storage/session_db` | 70% | `api/session/store.py` + `web_session_db.py` + `core/session/db.py` 三合一 |
| `storage/registry`（P-3） | 40% | `study_store.py` 索引设计借鉴，登记表新写 |
| `storage/migration` | **90%** | `core/storage/event_schema.py`（user_version + ensure/migrate 模式）直接扩展 |
| 业务域（backtest/alpha_zoo/hypothesis/journal/goal/memory/permission/hooks/skills/validation/portfolio/data_source/db/observability/utils） | **~99%** | 几乎零改动（memory 95% 待 B5、llm 95% 待 A1 限流层） |

### 5.2 总账

| 类别 | 占比 | 内容 |
|---|---|---|
| **真正的新代码** | **~20–25%** | E1（已交付一半）· Gateway/fence/GraphEngine 包裹/executor 池/P-3 登记 |
| **搬位置 + 去耦合 + 合并重复** | **~75–80%** | loop→react 节点 · phase_engine→preset · 4 builder→hub · 3 队列→mailbox · 3 调度→1 |

**结论**：17d 估算可行——不是重写 28k 行，而是**重组它们**。

### 5.3 三个「合并即收益」的热点（优先做）

1. **4 个 prompt builder → ContextHub**（85% 复用，消除 3 套 token 估算）
2. **3 处调度 → gateway/scheduler**（60% 复用，消除「各做各的」）
3. **loop.py → react 节点**（90% 复用，纯搬运 + 少量上交，最大单笔代码移动）
