# 新架构代码与测试矩阵梳理 — 2026-10-26

> ⚠ **历史文档（已过时）**：静态清单。当前数据见 `project-status-and-roadmap-20261028.md` §1。

> 范围：`core/orchestration/`（E1 契约层）· `core/gateway/`（Gateway 本体）· `core/runtimes/`（执行面）。
> 用途：当前实现全貌 + 测试覆盖对照 + 已知缺口（下一刀依据）。

---

## 1. 代码盘点（20 个模块 · 1480 行）

### core/orchestration/ — 跨界契约（业务无关）
| 模块 | 行 | 职责 | 测试 |
|---|---|---|---|
| contracts.py | 101 | WorkRequest/QueueMode/Receipt/校验 | test_entry_contracts(+extended) 42 |
| mailbox.py | 180 | 排队域（FIFO/interrupt/cap/deadline/steer 桩） | 同上 + behavior 部分 |
| entry_point.py | 71 | 分桶/惰性建会话/幂等索引 | **test_entry_point 19** |
| config_hub.py | 148 | 分层装载/fail-fast/指纹 | test_config_hub 12 |
| budget_gate.py | 70 | session 层预算合并 | test_budget_gate 10 |
| branch_contracts.py | 55 | RoundOutcome/BranchContext 中立协议 | test_branch_contracts 8 |

### core/gateway/ — Gateway 本体
| 模块 | 行 | 职责 | 测试 |
|---|---|---|---|
| session_controller.py | 79 | 准入/身份签发/驱动引擎 | **test_session_controller 23** |
| fencing.py | 57 | activeRunId 认领+提交校验 | test_fencing 8 |
| invariants.py | 97 | INV-1/3/5/8/9 注册表+自检 | test_invariants 5 |
| entry_adapters.py | 57 | 四源请求翻译 | **test_entry_adapters 38** |
| gateway.py | 49 | 内核组装（EntryPoint+Controller+Fence） | test_gateway_behavior 23 |
| singleton.py | 28 | 进程内唯一实例 | test_gateway_singleton 4 |

### core/runtimes/ — 执行面
| 模块 | 行 | 职责 | 测试 |
|---|---|---|---|
| graph_engine.py | 42 | langgraph 线性图执行 | test_graph_engine 9 |
| nodes/llm.py | 40 | 单次 LLM 调用（含预算） | test_llm_node 14 |
| nodes/react.py | 79 | 工具调用循环 | test_react_node 8 |
| run_budget.py | 45 | 运行时预算唯一入口 | **test_run_budget 20** |
| executor_bridge.py | 185 | 常驻子进程池 | test_executor_bridge 10 |

---

## 2. 测试矩阵（337 非 live + 15 live）

| 层 | 文件 | 条数 |
|---|---|---|
| 契约 | test_entry_contracts / _extended | 42 |
| 契约 | test_entry_point | 19 |
| 契约 | test_config_hub / budget_gate / branch_contracts | 30 |
| 契约 | test_orchestration_finishing_extended | 16 |
| Gateway | test_gateway_behavior | 23 |
| Gateway | test_gateway_singleton | 4 |
| Gateway | test_gateway_entry（adapters+组装） | 15 |
| Gateway | test_session_controller | 23 |
| Gateway | test_entry_adapters | 38 |
| Gateway | test_fencing | 8 |
| Gateway | test_invariants | 5 |
| 执行面 | test_graph_engine / llm_node / react_node | 31 |
| 执行面 | test_run_budget | 20 |
| 执行面 | test_executor_bridge | 10 |
| 端到端 | test_stage1a_slice | 5 |
| 入口迁移 | test_scheduler_gateway_path / cli_gateway_path | 7 |
| **live（opt-in）** | test_llm_live 10 · react_live 3 · entry_migration_live 2 | **15** |

---

## 3. 三轮补测抓到的真 bug（全部无测试则不可见）

| # | bug | 位置 | 严重度 |
|---|---|---|---|
| 1 | llm 节点完全绕过预算检查 | runtimes/nodes/llm.py | **HIGH** |
| 2 | workers=1 可被直接构造绕过 | orchestration/config_hub.py | MEDIUM |
| 3 | executor cancel 杀死空闲池 | runtimes/executor_bridge.py | MEDIUM |
| 4 | 外部 sink 屏蔽内部事件缓冲 | gateway/gateway.py | MEDIUM |
| 5 | session_key 校验只查冒号存在 | orchestration/contracts.py | MEDIUM |

**共同特征**：都是「边界/组合」问题，Happy-path 测试永远不会发现。

---

## 4. 已知缺口（下一刀依据）

| 缺口 | 优先级 | 归属 |
|---|---|---|
| **数据面未落地**：每 session 分库、PlatformStore、P-3 登记、键控迁移 | **高** | 阶段 3b |
| SSE 双轨未归一（sse_buffer + bridge_v2 并存） | **高** | 阶段 3b |
| 灰度 flag（QNODES_NEW_ENTRY）未移除，旧路径未删 | 中 | 阶段 3 收尾 |
| ContextHub 未实现（4 builder 仍在各自拼） | 中 | 阶段 4 |
| 三处调度未归一（网关 scheduler 还是空的） | 中 | 阶段 4 |
| preset 注册表未实现（chat/research/fanout） | 中 | B1 |
| ExecutionProfile 未实现（execute 字段恒 None） | 低 | B1 |
| T3 沙箱第二段（WSL2 bubblewrap） | 低 | P3 前 |
| Windows Job Object 资源上限 | 低 | A1 平台层 |
| 测试隔离修复（全量套件顺序依赖） | 中 | 阶段 4 |

---

## 5. 架构一致性现状（对照设计文档）

| 设计承诺 | 实现状态 |
|---|---|
| 每 session 一自包含 SQLite | ❌ 未实现（阶段 3b） |
| event_log 全量永久保留 | ❌ 未实现（阶段 3b） |
| 唯一写入者 PlatformStore | ❌ 未实现（阶段 3b） |
| fence activeRunId | ✅ 已实现 + 测试 |
| 不变量层 INV-1/3/5/8/9 | ✅ 已实现 + 自检 |
| mailbox 排队域（cap/steer/deadline/interrupt） | ✅ 已实现 |
| 四层派生身份（session→turn→run→子） | ✅ turn/run 已实现；子 run 待 fanout |
| GraphEngine 唯一执行体 | ✅ 线性图已实现；preset 待 B1 |
| executor 常驻进程池 | ✅ 已实现（Job Object 待平台层） |
| 预算唯一入口 | ✅ 已实现（INV-9 守护） |
| 入口统一 WorkRequest | ✅ 三入口灰度；MCP 非入口 |
| 宿主挂载式单进程 | ⚠ 事实成立（FastAPI 挂载 Gateway）；`qs gateway` 启动器未做 |

---

## 6. 下一步建议

**阶段 3b（数据面）是当前最大缺口**——设计文档承诺的存储形态完全未落地，
而它是「事实流唯一真源」这一整套论证的物理基础。

建议顺序：
1. `core/storage/platform_store.py`：唯一写入者（event/message/entity + blob 去重）
2. 每 session 分库 + 全局登记库
3. SSE 单轨（删 sse_buffer/bridge_v2）
4. 灰度 flag 移除 + 旧路径删除
