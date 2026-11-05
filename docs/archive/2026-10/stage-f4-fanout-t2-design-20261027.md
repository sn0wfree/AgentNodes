# F4 设计 — fanout t2（executor 池分支）— 2026-10-27

> 前置：F1 复合节点 + F2 预算树 + F3 preset v1。
> 规则依据：执行面 §5.2 规则③「cpu 型子 agent → T2 executor 池 job」、
> 规则⑤「取消连坐、5s 升级硬杀」；§5.3 job 契约（四通道/pickle 白名单）。

---

## 1. 形态

`add_fanout(..., mode="t2", executor=bridge, cancel_timeout=5.0)`：
分支不再跑线程，而是**派发成进程池 job**。图结构不变（仍是复合节点）。

```python
JobSpec(job_id=branch_id,
        fn=_t2_branch_entry,            # graph_engine 模块级（可 pickle）
        args=(worker, payload, ctx))    # worker 必须模块级可导入
```

`_t2_branch_entry` 在**子进程**里执行：从 ctx 的 `budget_limits` 重建本地
RunBudget 放进 `branch["budget"]`（对象不跨进程），调用 worker，捕获
GraphInterrupt 转成可 picklable 的 `{"interrupt": {reason, data}}` 标记。

**worker 约束（如实声明）**：必须模块级可导入函数（进程池无法传 lambda/闭包/
句柄）；`ExecutorBridge.dispatch` 的 pickle 预检会给出可操作报错。

## 2. 失败与取消

| 情形 | 子进程内 | 主进程处置 |
|---|---|---|
| 业务异常 | 冒到 `_worker_main` | JobResult.ok=False → 分支 status="error"（不连坐，与 t1 一致） |
| GraphInterrupt | 被 `_t2_branch_entry` 捕获 | 转成中断标记 → 主进程**连坐**：`bridge.cancel(hard_timeout=cancel_timeout)`，未完成分支标 cancelled，join 不执行 |
| 排队中被取消 | worker 任务间隙查 cancel 事件 | `cancelled before start` → 分支 status="cancelled" |
| 硬杀 | 协作取消超时 | `terminate()` 杀 worker；`discard_pending()` 清计数；下次 dispatch `ensure_alive()` 重建池 |

**放弃在飞结果**：连坐后不再等待剩余 job（它们即使完成也无意义）；
`ExecutorBridge.discard_pending()` 重置在飞计数并清空结果队列——否则
后续任何 cancel 都会误判「有任务在飞」而白等 5 秒。

## 3. 预算（跨进程）

对象不共享，**限额共享**：父切片把 `limits` 传进子进程，子进程重建
RunBudget 本地执行（INV-9 在子进程内照常成立）；worker 若在结果里带
`_budget_usage = {tokens, prompt_tokens, completion_tokens, cost_usd}`，
主进程把这份消耗记账到父切片（settle 时随切片归还）。未带则视为零消耗
（cpu 型 job 通常按墙钟记账，token 维度不适用）。

父侧仍先预检**饥饿切片**（剩余为 0）：直接中断，不派发。

## 4. 红线：cpu 子 run 不得为 study round

**由构造保证，不加运行期校验**：今天不存在「把 study round 提交成 t2 job」
的 API——study 只经 turn 准入执行，fanout 的 worker 是任意函数，没有
升格为 turn 的通道。3c 接线 scheduler 时同样只走 mailbox。若未来出现
绕过路径，再补运行期标签校验（那时才有可校验的标签）。

## 5. 事件

job.started/completed/failed 由 ExecutorBridge 的 sink 发出（调用方构造
bridge 时接线）；引擎在 t2 收尾调用 `drain_events()` 冲刷队列。
主进程仍负责 `branch.settled`（父线程）。

## 6. 明确不做（YAGNI）

- 分池/池大小策略（单池 + 容量闸已定案）；
- 子进程内流式事件转发（bridge 四通道已具备，事件回流细节随 F5+ 需要再加）；
- t2 worker 的远程/沙箱执行（T3 属 P3）；
- 运行期 study-round 标签校验（见 §4）。

## 7. 验收

1. 声明校验：`mode="t2"` 缺 executor 拒绝；非法 mode 拒绝；t1 + executor 拒绝；
2. N 个模块级 worker job 并行执行、结果按 ordinal 保序合并；
3. 子进程业务异常 → status="error"，兄弟照跑；
4. 子进程 GraphInterrupt → 连坐：剩余标 cancelled、join 不执行、state 带中断；
5. 硬杀：长跑分支 + 中断分支并存，`cancel_timeout=0.5` 时池被 terminate，
   `discard_pending` 后池可自愈重建（后续 run 正常）；
6. `_budget_usage` 回流：父切片记账正确、settle 后父账正确。
