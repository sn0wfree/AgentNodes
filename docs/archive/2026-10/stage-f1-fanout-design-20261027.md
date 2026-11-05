# F1 设计 — fanout 复合节点（多 subagent 并行）— 2026-10-27

> 决策依据：五点已确认（2026-10-27）——D1 复合节点形态 / D2 业务失败默认
> abort_turn / D3 steer 挂起至 join / D4 分支粒度 v1 单 worker / D5
> max_parallel=8 与 lane 同源。
> 上位约束：`execution-plane-research-20261024.md` §5.2 多 subagent 七条规则
> （2026-10-24 定），本设计是其图引擎落地，不翻案。

---

## 1. 形态：复合节点（D1）

fanout 在图上是**两个普通节点**（拆分、汇合）+ 一条普通边；N 条并行分支
活在**拆分节点肚子里**，由引擎内建原语管理。对外不变量一条不破：

- 「每节点一条出边」不破——`add_fanout(source, worker, join)` 声明时把
  source 的出边**强制指向 join**（声明即覆盖，语义与 add_edge 一致）；
- langgraph 底层图保持直线（split → join），不碰它的并行超级步语义；
- 取消树、预算树的挂靠点 = 拆分节点（树根），与七条规则的树形模型一一对应。

## 2. API

```python
engine.add_fanout(
    source="split",      # 拆分节点（先 add_node 注册）；fn 返回 {"branches": [载荷, ...]}
    worker=worker_fn,    # worker(载荷, branch_state) -> dict（分支结果贡献，任意可 JSON 化 dict）
    join="join",         # 汇合节点（先 add_node 注册）；fn 从 state["fanout_results"] 读全貌
    mode="t1",           # "t1" 进程内并行（本阶段）；"t2" 进程池属 F4，声明期即拒绝
    max_parallel=8,      # = subagent lane 上限（D5 同源，默认 8）
)
```

- `branch_state = {**父state快照, "branch": {id, ordinal, payload}}`——分支
  在快照上干活，**返回值不自动并入父 state**（账本隔离）；汇合节点负责合并。
- `branch.id = {run_id}:{ordinal}`（P-4 子 run 树形身份，规则 2）；state 无
  run_id 时退化为 `{节点名}:b{n}`（测试/脚本场景）。
- 拆分返回非 list → 立即抛错（fail-loud）。

## 3. 两档失败（引擎交数据，preset 定策略）

| 档位 | 触发 | 引擎行为 |
|---|---|---|
| 业务失败 | 分支抛普通异常 | 该支记 `status="error"`（含错误串），**其余分支照跑**，汇合照常——join 看到全貌后自行执行 abort_turn（D2 默认策略属 preset 层，F3 落地） |
| 系统中断 | 分支抛 `GraphInterrupt`（预算/HITL/取消） | **连坐**：立取消旗 → 未起跑分支记 `cancelled` 不再执行；已起跑的 T1 分支**跑完当前单元**（Python 杀不掉线程，协作粒度=整个 worker，如实声明）；汇合**不执行**，state 带 `__interrupt__`（含 reason + 全部结果条目），路由守卫送图到 END |

结果条目契约：`{ordinal, branch_id, status: ok|error|cancelled|interrupted,
payload, result, error, seconds}`——`fanout_results` 按 ordinal 保序。

## 4. 事件（全部父线程发，sink 天然单线程安全）

| 事件 | 载荷 | 时机 |
|---|---|---|
| `graph.fanout` | {node, branches, max_parallel} | 拆分节点跑完用户 fn 后 |
| `branch.settled` | {node, branch_id, ordinal, status, seconds} | 每支落定（as_completed 顺序） |

分支内部的细粒度事件（react 工具调用等）属 F3：届时 worker 带事件队列，
回流后由父线程统一转发——本阶段 sink 不跨线程。

## 5. 与七条规则的对账

| 规则 | 落点 |
|---|---|
| 1 两级准入 | 分支不碰 mailbox；max_parallel 即 lane 闸（D5 同源） |
| 2 树形身份 | branch.id = {run}:{ord} |
| 3 两类两层 | mode="t1"（本阶段）；mode="t2" 接 executor 池（F4） |
| 4 预算树 | F2：拆分时预留、分支消费、join settle 归还（本阶段 worker 无预算参数，F2 加 branch.budget，非破坏） |
| 5 steer/interrupt 只认 turn | D3：fanout 期间 steer 挂起，join 后根的下一次模型调用注入（mailbox 挂根不变） |
| 6 事件带 run_id | branch.settled 带子 run_id；大结果走 artifact 通道（parent_id 血缘已备） |
| 7 子无逃逸 | 引擎无「分支升格 turn」API；cpu 分支不得为 study round（F4 落地时校验） |

## 6. 分期

| 期 | 内容 | 状态 |
|---|---|---|
| **F1** | 复合节点 + 两档失败 + 连坐取消 + 事件 + 测试 | **本阶段** |
| F2 | 预算预留树（reserve/consume/settle）+ PlatformStore 写线程锁 | 待 |
| F3 | fanout preset v1（react 子代理 + steer 挂起接线 + abort_turn 策略） | 待 |
| F4 | mode="t2"：executor 池分支 + 取消 5s 硬杀 + study-round 禁令校验 | 待 |

## 7. 明确不做（YAGNI）

- 分支=子图（D4：等账本约定成熟，F5+）；
- 图级多出边 / 汇合屏障原语（复合节点够用，rule of three）；
- 分支级 partial-merge / 动态追加分支；
- T1 分支硬杀（Python 不支持，如实协作取消）。
