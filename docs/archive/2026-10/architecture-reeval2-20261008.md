# 架构师重估（第二遍） — 2026-10

> ⚠ **历史文档**：架构师重估（第二轮）。结论已被 v6.2 取代。

> 目的：审计上一版 `architecture-reeval-20261008.md` 的结论本身，修正错误并给出更准的顺序。
> 方法：核对文件存在性 / 真实 import 扇入 / 反向依赖的使用点上下文。

## 0. 先更正自己的两处错误

### 错误 1：「web_session 已不在树上」——**错**

我只查了 `api/session/web_session.py`（确实不存在），漏了 `api/routers/web_session.py`（**637 行，存在**）。

事实：老 1503 行的 web_session **早已拆过**（`api/session/` 包已建：projector 278 / projection_store 355 / bridge_v2 70 / service 1451）。
→ **A 刀（web_session↔projector）不是"作废"，而是"已做完"**。残留体量挪到了 `api/session/service.py`（1451 行）——这才是真热点。

### 错误 2：把三条反向边当同一类问题——**错**

上一版把它们并列成"3 条 core→api 反向边"，给出"1 天断完"的乐观估计。逐条看代码后，三者是**三种不同病**：

| # | 反向边 | 真实病因 | 上一版处方 | 正确处方 |
|---|---|---|---|---|
| 1 | `scheduler → web_session._get_db` | **私有符号泄漏**：编排层调路由下划线函数拿连接 | "断反向边" | 改走 `core/session/db.SessionDB`；**30 分钟，零风险** |
| 2 | `langgraph_engine → bridge_v2` | **SSE 双轨未收敛**（见下） | "加订阅缝" | EventStore **已有** `subscribe()`；要的是**完成迁移**，不是加缝 |
| 3 | `event_store → projector` | **惰性 best-effort 物化**（有 except 兜底） | "断反向边" | 与 #2 一起处理：Projector 侧订阅，event_store 不认 projector |

## 1. 真正的结构发现：SSE 有两条通道

```mermaid
flowchart LR
  EM[Agent emit] --> ES[EventStore]
  ES -->|publish: _sse_pusher| LEG[SSEEventBuffer 旧通道]
  ES -->|subscribe() 原生| SUB[订阅者]
  LEG --> EP["/api/chat/events 读旧 buffer"]
  SUB -.尚未被端点使用.-> EP
  BR[bridge_v2] -.从引擎反向 import.-> LEG
```

- EventStore **已经有原生订阅面** `subscribe()`（replay + live 异步迭代器，7 处在用）
- 但 `/api/chat/events` 仍读**旧 `SSEEventBuffer`**
- `bridge_v2` 的注释自陈：「until that endpoint is migrated to read directly from EventStore.subscribe()」
- 于是引擎必须**从自己内部**反向 import bridge 把事件推进旧通道——**这才是 langgraph 反向依赖的根因**

**结论**：这不是"层次没管好"，是**一次做了一半的迁移**。旧通道只剩 3 个真实消费者（`chat.py` 端点、`slash_commands.py`、`bridge_v2.py`）。

**动作**（建议作为下一刀）：
1. `/api/chat/events` 改读 `event_store.subscribe()`
2. 删 `api/sse_buffer.py`、`api/session/bridge_v2.py`、EventStore 的 `_sse_pusher`
3. 引擎里的反向 import 自然消失
4. 顺带：SSE 从两轨变一轨

**收益等级**：这一刀同时拿到「消一条反向依赖 + 删一条双轨 + 端点简化」，比任何拆文件都值。

## 2. 校正后的热点表

| 文件 | 行 | 扇入 | 判定 |
|---|---|---|---|
| `core/goal/store.py` | 1773 | — | 大但边界清，**低优先** |
| `api/routers/study.py` | 1643 / 33 路由 | **29** | **改一处全表抖 → 第二刀** |
| `core/study/store.py` | 1619 | — | 低优先 |
| `api/session/service.py` | 1451 / 24 方法 | 10 | 老 web_session 的残体 → **第三刀** |
| `core/autoresearch.py` | 1393 | — | W-split 壳 + re-export → 第四刀 |
| `core/agent/loop.py` | 1307 / 40 方法 | — | 已注解止血，**触达式** |
| `api/session/projection_store.py` | 355 | **1** | **死代码候选**（随第三刀清） |
| `api/routers/web_session.py` | 637 | 15 | 已拆过，**不是热点** |

## 3. 校正后的顺序

| 序 | 刀 | 估工 | 性质 | 备注 |
|---|---|---|---|---|
| **0a** | `scheduler._get_db` → `core/session/db` | **0.5 h** | 私有符号泄漏 | 先摘低垂果实，建立信心 |
| **0b** | **SSE 双轨收敛**（端点改 `subscribe()`，删旧 buffer/bridge/_sse_pusher） | 1.5-2 天 | 完成半截迁移 | 最高杠杆：一刀三收 |
| **1** | `routers/study.py` 按面切（lifecycle / query / artifacts / agents） | 2 天 | 控制面无边界 | 扇入 29，改一处全表抖 |
| **2** | `session/service.py` 拆（Queue / AttemptRunner / Compactor）+ 清 `projection_store` 死码 | 2 天 | 残体收口 | |
| **3** | `autoresearch.py` 消包（阶段函数归位，删 re-export） | 1 天 | 名不副实 | |
| **4** | goal / study store 读写分仓 | 2 天 | 体量大但边界清 | 低优先 |
| — | AgentLoop 去 kernel | 触达 | 已止血 | 不专项 |

## 4. 原则修订（增补上一版）

1. **依赖只准向下**：`core/**` 禁 import `api/**` / `cli/**`（CI 加 import 门禁）
2. **传输层单轨**：一个事件源只允许一条对外通道；兼容层必须带「迁移截止」标记（bridge_v2 已自带注释，但没人执行）
3. **引擎不感知传输**：`emit` 是唯一出口；订阅方在组合根注册，不在引擎内部拉线
4. **router 无业务**，≥15 路由必须分模块
5. **Service 按生命周期切**，不按方便堆
6. **W-split 完成的定义**：原文件只剩 re-export 就删（`autoresearch.py` 是当前最明显违规）

## 5. 建议的成功度量（替换上一版）

| 度量 | 目标 |
|---|---|
| `core → api` 反向 import | **0**（当前 3） |
| 对外事件通道 | **1**（当前 2） |
| 单文件路由数 | ≤ **12**（`study.py` 当前 33） |
| 上帝服务方法数 | ≤ **12**（`SessionService` 当前 24） |

比"巨文件 <800 行"硬，且都能机械验证。

## 6. 与上一版的分歧（已收敛）

| # | 上一版 | 本版 | 理由 |
|---|---|---|---|
| 1 | 反向依赖优先 | **维持**，但拆成 0a/0b，且 0b 的处方从"加缝"改为"完成迁移" | EventStore 已有 subscribe |
| 2 | A 刀作废（对象没了） | **订正**：A 已做完，残体在 `session/service.py` | 查错了路径 |
| 3 | projection_store 死候选 | **维持**（扇入 1） | 复核 fan-in 确认 |
| 4 | 成功度量 | **替换**为四条可机械验证的 | "行数"易被形式主义消解 |
