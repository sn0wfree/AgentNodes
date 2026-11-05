# F3 设计 — fanout preset v1（react 子代理并行）— 2026-10-27

> 前置：F1 复合节点（`5ff23507`）+ F2 预算树（`f8b8122d`）。
> 决策依据：D2 业务失败默认 abort_turn、D3 steer 挂起至 join、D4 分支粒度
> v1=单 worker、D5 max_parallel=8（均 2026-10-27 拍板）。
> 上位约束：执行面文档 §5.2 七条规则。

---

## 1. 先修一个 F2 暴露的真缺陷（必须，属本阶段）

**react 节点忽略传入的 RunBudget**：`make_react_node` 内部 `RunBudget(limits=...)`
新建对象，`branch_state["branch"]["budget"]`（切片）根本没被用上——F2 的预算树在
react 分支里**形同虚设**。修法：

```python
ReactConfig.budget: RunBudget | None = None   # 注入式：切片优先
# 节点内：用注入的预算对象（同一实例，消耗直接记账到切片），否则按 budget_limits 新建
```

`budget_limits` 保留（向后兼容既有 2 处调用）；两者同时给时 `budget` 优先（切片
即父账本的子树）。这是「继承既有预算对象」而非「复制限额」——只有这样 settle
才能拿到真实消耗。

## 2. fanout preset：`build_fanout_preset(...)`

落位 `core/runtimes/presets/fanout.py`（纯平台件：只依赖 GraphEngine/react 节点/
RunBudget，不认识 study —— 与 research preset 的域归属相反，因为 fanout 是通用
执行形状，chat/DAG/研究都能用）。

```python
build_fanout_preset(
    client, registry, *,
    split: Callable[[dict], list],      # 父 state -> 分支载荷列表
    workers: int = 1,                    # 每分支幾次 react 循环？v1=1（一次任务）
    system: str | None = None,
    max_iterations: int = 8,
    max_parallel: int = 8,               # = subagent lane（D5）
    failure_policy: str = "abort_turn",  # D2；"continue" 备选
    join_fn: Callable[[dict], dict] | None = None,
) -> GraphEngine
```

图形态（仍是两普通节点，D1 不破）：

```
start(注入 state) → split(fn=split) →[fanout 复合]→ join(fn=join_fn or 默认合并)
```

**worker**（每条分支）：持切片预算构造 react 节点，每条分支是**独立子代理**：

```python
def branch_worker(payload, branch_state):
    budget = branch_state["branch"]["budget"]
    node = make_react_node(client, registry, sink=..., config=ReactConfig(budget=budget, ...))
    out = node({"messages": [{"role": "user", "content": str(payload)}],
                "run_id": branch_state["branch"]["id"]})
    if out["stop_reason"].startswith("budget:"):
        raise GraphInterrupt("branch_budget_exhausted", {"branch": ...})   # 连坐（系统档）
    return {"answer": out["answer"], "stop_reason": out["stop_reason"],
            "usage": out["usage"]}
```

**join 默认合并**（`failure_policy="abort_turn"`，D2）：

| 分支结果 | join 行为 |
|---|---|
| 全 ok | `{"fanout_answers": [...], "fanout_ok": True}`（按 ordinal 保序） |
| 有 error | `{"fanout_ok": False, "fanout_errors": [...], "aborted": True, "reason": "branch_failed"}` —— **整轮标红**（保守默认），join 后续节点仍会跑，由调用方看 `fanout_ok` |
| 有 interrupted | 引擎已连坐：join 不执行，state 带 `__interrupt__`（F1 语义） |

`failure_policy="continue"` 时 error 分支被跳过、其余结果照常合并（Tier-1 提案档位
预留，v1 默认不启用）。

## 3. steer 挂起至 join 后注入（D3）

**v1 边界**（如实声明，不假装实现）：

- fanout 期间到达的 steer **不进分支**（同一句话进 N 份子上下文语义混乱）；
- 引擎层提供 `state["steer_after_join"]: list[str]` 约定键：join 节点把
  `steer_after_join` 里的文本原样追加为一条 `user` 消息到 `messages`，供后续
  react/llm 节点消费；
- **真正的 mailbox 侧挂起/恢复接线留到 3c**（控制器撤销降级、把 steer 塞进该键）——
  本阶段只交付「引擎/preset 侧接得住」的承载点，不谎报端到端；
- 事件：join 注入时发 `steer.injected_after_join {count}`。

## 4. 子代理事件回流（规则⑥的前置）

分支 worker 的 react 节点事件（tool.started 等）来自**分支线程**。v1 用
`queue.Queue` 收集、join 后在**父线程**统一转发给引擎 sink：

```
worker sink = queue.put
join 后（或引擎 fanout 收尾）→ 逐条 sink(kind, {**data, "branch_id": ...})
```

引擎侧已保证 `branch.settled` 在父线程发；分支内部事件 v1 转发是**尽力而为**
（先收集后转发，顺序保持每支内部有序）。v2 若需实时回流（WebUI 实时看子代理进度），
再给引擎 sink 加线程安全队列（属 F5+，本阶段不做）。

## 5. 明确不做（YAGNI）

- 分支=子图（D4）；分支再开 fanout（F5+）；
- mailbox 侧 steer 挂起/恢复全链（3c）；
- 实时分支事件流（F5+）；
- failure_policy 的 retry/quorum/reduce 档（Tier-1 提案通道，D2）。

## 6. 验收

1. **预算真生效**（修复回归）：分支 react 节点消耗记账到切片；父 settle 后总量正确；
   切片耗尽 → 分支抛 GraphInterrupt → 连坐（join 不执行）；
2. 三个分支真 LLM（live 可选）：各自独立回答 → join 按序合并；
3. error 分支：`failure_policy="abort_turn"` → `fanout_ok=False` + `aborted`；
   `"continue"` → 跳过坏分支、其余合并；
4. steer 承载点：`steer_after_join` 注入 messages + `steer.injected_after_join` 事件；
5. 分支事件回流：分支线程的 tool.started/completed 在父线程 sink 可见（带 branch_id）；
6. `max_parallel` 默认 8 与 lane 同源。
