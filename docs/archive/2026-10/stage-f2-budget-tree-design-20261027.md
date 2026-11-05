# F2 设计 — 预算预留树 + PlatformStore 写线程锁 — 2026-10-27

> 依据：执行面文档 §5.2 规则④「父预留、子消费、settle 归还」；
> F1 fanout 复合节点（`stage-f1-fanout-design-20261027.md`）的前置补全。

---

## 1. 预算树形切分（RunBudget.reserve / absorb）

```
父 RunBudget（limits L，usage U）
  reserve(n)  →  n 个子切片：limits = 剩余额度均分（floor，余量留父）
  fanout 期间：每分支只碰自己的切片（独立对象、各线程一份）→ 热路径无锁
  absorb(*slices)：父 usage += Σ子实际消耗（settle 归还）
```

| 维度 | 切分 | settle |
|---|---|---|
| max_tokens | `(L-U)//n`，floor | usage 求和 |
| max_cost_usd | 剩余均分 | cost 求和 |
| max_seconds | 剩余秒数均分 | **不吸收**——分支并行跑在父的墙钟里 |
| price 字段 | 原样透传（成本记账要靠它） | — |

**关键坑（实测发现的设计约束）**：`evaluate_budget` 把上限 `0 / None` 都视为
「维度未启用 = 无限」。所以「剩余为 0 的切片」**不能**写成 0 上限（那是无限），
必须**预标 exhausted**——`plan_call` 首行短路返回拒绝。这是本阶段对
run_budget 唯一的行为修改：已 exhausted 的对象再问 plan_call 直接说不行
（此前是每次重评估，语义等价但挡不住 0-上限漏洞）。

**语义边界**：
- 子切片耗尽 ≠ 父预算耗尽——exhausted 不回传，父后续 plan_call 按自身
  限额自然判定；
- 未预留余量（L - n×切片）在 fanout 结束后仍归父（limits 不动）；
- 切片内分支对 INV-9 依旧成立——切片本身就是 RunBudget，worker 持切片
  构造 llm/react 节点（`make_llm_node(client, budget=切片)`），调用照走
  plan_call；
- 均分是 v1（七规则④）；weighted/reserved 档属 Tier-1 提案通道（D2）。

## 2. fanout 接线（state 约定键 `run_budget`）

```
state["run_budget"] 是 RunBudget 实例？
  ├─ 是：_run_fanout 开场 reserve(n)，第 i 支分支拿 slices[i] 放进
  │      branch_state["branch"]["budget"]；全部落定后父线程 absorb(*slices)
  └─ 否：branch.budget = None，worker 自行决定（无预算需求零打扰）
```

图作者（preset 构建方）负责把父 RunBudget 放进 state；引擎只认约定键，
不做隐式创建。中断/业务失败的条目语义不变——预算中断是 worker 抛
GraphInterrupt（切片 plan_call 拒绝后由 react 类 worker 转化），连坐规则
沿用 F1。

## 3. PlatformStore 写线程锁

F1 的事件全部父线程发，store 本无并发；F3 起 react 子代理的事件要跨线程
回流，而 sqlite3 连接默认 `check_same_thread=True`（跨线程即抛，Gateway
会吞掉→事件静默丢失）。

改法（对应已定案「每 session 单写连接串行化短事务」）：
- 两个连接构造加 `check_same_thread=False`；
- Store 级 `threading.Lock` 包住**全部公开方法**（emit/replay/latest_seq/
  store_blob/read_blob/register_*/list_*/quick_check/close）——串行化短事务，
  单写者不变量 7 从纪律变物理事实；
- 读也走同一把锁：sqlite 连接对象本身不允许并发使用，读写互斥最简且够快
  （单机单用户，事务都是毫秒级）。

## 4. 明确不做（YAGNI）

- 加权/按需预留档（Tier-1 提案）；
- 切片再切（孙辈预算树——fanout 分支再开 fanout，等子图 F5+）；
- 切片级事件（budget_exhausted 已由 react 节点发，不重复造）；
- Store 读写锁分离/连接池（单用户毫秒事务，Lock 足够）。

## 5. 验收

1. 均分数学：10 tokens 切 3 → 3/3/3，余 1 留父；已用 4 剩 6 切 3 → 2/2/2；
2. 零剩余切片 plan_call 拒绝（0-上限漏洞被预标 exhausted 挡住）；
3. 未启用维度透传 None（父无限 → 子无限）；
4. 切片独立：A 记账不动 B；absorb 求和、时间与 exhausted 不回传；
5. fanout 集成：state 带父预算 → 分支拿到切片、settle 后父 usage=Σ子；
   state 无预算 → branch.budget=None 零打扰；
6. PlatformStore：8 线程并发 emit 全部落库无丢失。
