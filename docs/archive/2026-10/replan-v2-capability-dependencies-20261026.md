# 规划修订 v2 — 入口迁移的能力依赖 — 2026-10-26

> 触发：阶段 3b-3 把 `new_entry_enabled()` 默认改为开后，**12 个 scheduler 测试失败**。
> 根因：study 执行依赖 research preset（GraphEngine 跑阶段图），属 B1；当前新路径只有 llm 节点。
> 结论：**入口迁移不能按「来源」排期，必须按「能力依赖」排期**。本文件修订 replan。

---

## 1. 失败根因（完整链条）

```
scheduler.submit()
   ├─ 旧路径：投 per-session asyncio.Queue + _ensure_consumer()
   │           → 消费者协程调 AutoresearchRunner → study 真正执行
   └─ 新路径：submit_via_entry(WorkRequest) 进 mailbox 后 **return**
                → 没人消费；即使有人消费，GraphEngine 只有 llm 节点
                → study 永远停在 QUEUED
```

**测试只覆盖了「投递成功」（mailbox 深度=1），没覆盖「执行等价」。**
这是迁移测试的方法论缺陷：**投递 ≠ 功能等价**。

## 2. 能力依赖矩阵（本文件的核心）

| 入口 | 迁移所需能力 | 当前状态 | 能否迁移 |
|---|---|---|---|
| **API chat `/send`** | llm 节点（单轮问答） | ✅ 已有 | ✅ 已迁 |
| **CLI TUI** | llm 节点 + MemoryManager | ✅ 已有 | ✅ 已迁 |
| **scheduler / study** | **research preset**（阶段图：假设→回测→评估→journal）+ executor 集成 + 证据落盘 | ❌ 缺（B1） | ❌ **不能** |
| **MCP** | 非入口（工具协议服务器） | — | N/A |
| **automation（定时研究）** | 同 scheduler | ❌ 缺（B1） | ❌ **不能** |

**这条依赖此前是隐式的（凭经验知道），现在显式化。**

## 3. 双门灰度（已实施）

| 门 | 环境变量 | 默认 | 适用范围 |
|---|---|---|---|
| `new_entry_enabled()` | `QNODES_NEW_ENTRY` | **开** | chat / CLI |
| `study_entry_enabled()` | `QNODES_NEW_ENTRY_STUDY` | **关** | scheduler / automation |

study 门在 **B1 完成后**翻默认开。

## 4. 修订后的阶段顺序

### 变更点
原顺序：`1a → 1b → 2 → 3a(四入口) → 3b(数据面) → 4(清理) → B1`
新顺序：**把 B1 的「research preset」提前到 scheduler 迁移之前**。

### 新阶段表

| 阶段 | 内容 | 依赖 | 工期 | 验收 |
|---|---|---|---|---|
| ✅ 1a | 纯 LLM 切片 | — | 2d | 真实对话走八站 |
| ✅ 1b | react 节点 + 工具树 | 1a | 2d | 真实工具调用（491 验证） |
| ✅ 2 | fence + A5 + executor + 预算统一 | 1b | 4d | 并行/取消/fence；审计门① |
| ✅ 3a | chat / CLI 入口迁移 | 2 | 4d | 两入口灰度（默认开） |
| ✅ 3b-1 | PlatformStore（分库 + 唯一写入者） | — | 2d | 17 测试 |
| ✅ 3b-3 | 双门灰度 + engine_factory 升级 | 3a, 3b-1 | 0.5d | 19 测试 |
| **3b-4** | blob/artifact 通道（next） | 3b-1 | 0.5d | 登记行可写可查 |
| **3b-2** | SSE 单轨（延后） | 3b-1 | 1d | test_sse_* 全绿 |
| ✅ B1-a | **GraphEngine 支持阶段图**（多节点 + 条件边 + 中断） | 1b | 2d | 两阶段图（gen→judge 环）跑通；设计见 `stage-b1a-graph-engine-design-20261027.md` |
| ✅ B1-b | **research preset**（phase_engine → 阶段图，第三面门 `QNODES_RESEARCH_GRAPH` 默认关） | B1-a | 3d | 旧/图两路径 RoundOutcome 逐字段等价；设计见 `stage-b1b-research-preset-design-20261027.md` |
| ✅ F1 | **fanout 复合节点**（多 subagent 并行，两档失败 + 连坐取消；D1-D5 已拍板） | B1-a | 2d | 15 测试：并行/隔离/中断连坐/并发上限；设计见 `stage-f1-fanout-design-20261027.md` |
| ✅ F2 | 预算预留树（reserve/absorb）+ PlatformStore 写线程锁 | F1 | 1d | 均分/零剩余预标/settle 归还 14 测试 + 8 线程并发 emit 全落库；设计见 `stage-f2-budget-tree-design-20261027.md` |
| ✅ F3 | fanout preset v1（react 子代理 + 失败策略 + steer 承载 + 事件回流） | F2 | 1.5d | 19 单测 + 2 live（真 LLM 三子代理并行）；**修 react 忽略注入预算的真缺陷**；设计见 `stage-f3-fanout-preset-design-20261027.md` |
| ✅ F4 | fanout mode="t2"：executor 池分支 + 取消 5s 硬杀 + 跨进程预算结算 | F1 | 1.5d | 12 测试：子进程执行/中断连坐/硬杀后池自愈；设计见 `stage-f4-fanout-t2-design-20261027.md` |
| ✅ 3c | **scheduler / automation 入口迁移**（kind 归属消费 + mailbox 消费者 + 双门翻默认开） | B1-b, F1-F4 | 1d | 17 测试含执行等价；设计见 `stage-3c-scheduler-migration-design-20261027.md` |
| **4** | 清理（ContextHub + 调度归一 + 反向依赖 + 测试隔离 + 门禁） | 3c | 7d | 净删 2000+ 行；审计门② |

**总工期调整：** 原 ~22d → **~24d**（B1-a/B1-b 的 5d 是新增的显式成本，原先隐含在「B1 编排」里没单列）。

## 5. 迁移测试方法论（本次教训固化为规则）

**规则：入口迁移的验收必须包含「执行等价」，而非仅「投递成功」。**

| 层次 | 验证什么 | 缺失后果 |
|---|---|---|
| 投递 | WorkRequest 进了正确 mailbox | 只证明路由对 |
| **执行** | 该 kind 的工单在新路径下**产出一致的业务结果** | **投递成功但功能退化（本次失败）** |
| 回退 | flag 关时旧路径行为不变 | 无法安全回退 |

**具体做法**：每个入口迁移时，从**旧路径的既有测试**里挑出「业务结果断言」类的用例，在新路径上重跑一遍——而不是只写新的结构测试。

## 6. 已落地的修正

- ✅ `study_entry_enabled()` 独立门（默认关）
- ✅ scheduler.py 改用 study 门
- ✅ 19 个双门独立性测试
- ✅ `engine_factory` 从 monkey-patch 升级为构造参数
- ✅ Gateway 接线 PlatformStore（data_dir 参数）

## 7. 待办

1. [x] 3b-4：blob/artifact 通道
2. [ ] 3b-2：SSE 单轨
3. [x] B1-a：GraphEngine 多节点 + 条件边 + 协作中断（`stage-b1a-graph-engine-design-20261027.md`）
4. [x] B1-b：research preset（`stage-b1b-research-preset-design-20261027.md`；轮次级等价已证明，提交级接线属 3c）
5. [x] F1：fanout 复合节点（`stage-f1-fanout-design-20261027.md`；D1-D5 已拍板）
6. [x] F2：预算预留树 + Store 写锁（`stage-f2-budget-tree-design-20261027.md`）
7. [x] F3：fanout preset v1（`stage-f3-fanout-preset-design-20261027.md`；含 react 预算注入修复）
8. [x] F4：fanout t2（`stage-f4-fanout-t2-design-20261027.md`；含 bridge 硬杀后队列复用真 bug 修复）
9. [x] 3c：scheduler 迁移（`stage-3c-scheduler-migration-design-20261027.md`；kind 归属消费堵住串道事故）

## 8. 附：本次失败的可复用教训

1. **flag 默认值必须匹配能力就绪度**——能力未到就默认开 = 功能倒退
2. **投递成功 ≠ 功能等价**——迁移测试必须覆盖业务结果
3. **隐式依赖要显式化**——「study 迁移依赖 research preset」应写在依赖矩阵里
