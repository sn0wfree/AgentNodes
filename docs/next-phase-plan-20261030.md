# 下一阶段规划：阶段 D（研究域收尾）（2026-10-30）

> 输入：v6.3 架构图状态框 + R1–B3/接缝/覆盖率工作后的实测。
> 性质：接替 `roadmap-next-20261030.md`（R1–R6 已全部收口）。

---

## 1. 架构图完成度评估（2026-10-30）

| 层 | 完成度 | 证据 |
|---|---|---|
| ① 渠道 | **100%** | CLI/TUI/API/schedule 四渠道全走 WorkRequest；MCP 无 chat 出口（N/A） |
| ② Gateway | **~95%** | mailbox 全语义（steer/老化/interrupt/cap/deadline）✅；控制器（准入+身份+fence+串行域+消费者+取消+状态查询）✅；Director 确定性版 ✅。缺：容量组闸语义（等价物已有，无痛点）|
| ③ 执行面 | **~90%** | GraphEngine 唯一编排（llm/react/fanout-t1/t2+节点边界取消）✅。缺：独立宿主进程（无触发）。⏹ 8 项判定不做 |
| ④ 平台数据面 | **~85%** | 统一库事件化 ✅ · P-3 登记 ✅ · P-5 指针 ✅ · PROTOCOL_VERSION ✅ · fold 重建 ✅。缺：每 session 分库（D2 延后）· 全局 session 登记（随分库）|
| ⑤ 研究域 | **~80%** | 账本/方法论运行 ✅ · Director 停因建议 ✅ · **循环导入已解**（contracts 下沉）。缺：**方法论归位 `core/study/method/`（前置刚清掉，可做）** |
| 接缝 | **100%** | 单向门禁 + 9 触点白名单 + 接缝契约（新增，图中还没有格子）|
| 测试 | A 91.2% / B 87.5% / C 88.5% | 分档门禁就位（新增，图中还没有）|

**总评**：平台侧（①②③④+接缝）已按图收口——所有格子要么 ✅、要么 ⏹ 有据、
要么 ⏸/⏳ 有明确触发条件。剩余实质工作集中在 **⑤ 研究域**：方法论归位。

## 2. 关键变化：方法论归位解除阻塞

`phase_engine ↔ runner` 循环导入已解（`c7838e78`，共享符号下沉
`study/contracts.py`，AST 门禁防回潮）。图上 ⏸ 的那条「前置=解循环导入」
**已满足**——这是当前唯一可立即开工的架构图遗留项。

## 3. 阶段 D 任务（按优先级）

| # | 事项 | 工期 | 前置/触发 | 说明 |
|---|---|---|---|---|
| **D1** | **方法论归位 `core/study/method/`** | 1-2d | ✅ 前置已清 | `phase_engine.py`（36KB，round 生命周期）+ `aegis`/`claims`/`guidance`/`metric_targets` 等**方法论域件**搬入 `method/`；`runner`/`scheduler`/`graph_preset` 留在 `engine/` 侧。纯搬家：import 改路径 + re-export，零行为变化。验收：architecture gates 全绿 + study 测试不破 + 触点白名单更新 |
| **D2** | **P5-1c 删旧 chat 分支** | 0.5-1d | ⏸ 触发=11-06（R2 上线一周） | 入口 `todo-remove-legacy-chat-branch-20261027.md` §0.3，第一步 ⓿ 删死代码 `_run_agent_loop_background`。删完后：Gateway 成为唯一 chat 路径，`new_entry_enabled` 门退役，ContextHub「独占」达成（图上最后一格翻 ✅） |
| **D3** | B/C 覆盖率冲 90%（集成测试） | 1-2d | 可选 | 剩余缺口集中在异步执行路径（scheduler watchdog、runner round 内层）——用集成测试（真实跑 study round/swarm DAG）覆盖，而非 mock 单测。可选：先删 `_archived/` 1,485 条死代码，全库口径直接 +3% |
| **D4** | 104 条历史基线失败清偿 | 持续 | 每刀顺手（D4 原则） | 本轮已顺手清 1 条（hanging_events context manager）|
| D5 | 容量组闸 / 独立宿主 / 分库 | — | 触发条件见接缝契约 §5 | 不主动做 |

**建议顺序：D1 → D2**（都在研究域/接缝附近，且 D2 有日历触发点）；
D3 视 D1/D2 后的覆盖率复测结果决定。

## 4. D1 拆法预研（方法论归位）

搬家清单（按消费方判断，「消费方在研究域、且是实现方法论词汇的」）：

| 去 `method/` | 理由 |
|---|---|
| `phase_engine.py` | round 生命周期 = 方法论的执行骨架 |
| `aegis.py` / `runner_aegis.py` | AEGIS 新颖性/回归门 = 方法论核心 |
| `claims.py` | 假设→证伪 claims |
| `guidance.py` | 研究引导 |
| `metric_targets.py` | 指标目标（验收标准） |
| `review_loop.py` | 评审循环 |

留 `engine/`（或原地）：`runner.py` / `scheduler.py` / `graph_preset.py` /
`store.py` / `state_store.py` / `bootstrap.py` / `monitor.py`（执行机制）。

**关键约束**：`runner → phase_engine` 的 import 在解环后已是单向——
搬家后变成 `engine/runner → method/phase_engine`，方向依然合法
（执行调用方法）。`STUDY_PLATFORM_TOUCHPOINTS` 白名单需同步更新文件路径。

## 5. 图的同步项（随 D1 提交）

- 状态框补三行：接缝加固 ✅ · 循环导入已解 ✅ · 覆盖率分档门禁 ✅
- ⑤ 研究域「方法论归位」从 ⏸ 改 ⏳（前置已清）
- 新增接缝格子（平台/研究域边界 = 门禁看守）
