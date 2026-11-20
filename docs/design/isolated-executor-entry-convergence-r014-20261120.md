# 隔离执行入口收敛（候选 4）R-014 方案

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **前置**：隔离执行器深化两提交已验收（`2f1daada` / `c3a2a417`）；
> 架构复审候选 4（Worth exploring）。用户 2026-11-20 裁决「只做 #4，#3 已由
> from_seam_result 部分收口、继续合并收益有限」。
> **范围**：只收敛**编排重复**——不改 wire 协议、不改 actual_state 词表、
> 不改毒化语义、不改对外 interface（isolate_tool_call / IsolationWorker.call）。

## 0. 目标与非目标

**目标**：把 `isolate_tool_call` transient 分支里重复的「spawn → channel →
start_reader → execute → 确认 → close」编排，改为复用 `IsolationWorker`
（resident 执行体）——一处编排、一处毒化判定、一处确认先于释放。

**非目标**：① 不改 `IsolationResult` / `IsolatedOutcome` 字段（候选 3 不动）；
② 不改 per-call id / codec / ctx_v；③ 不改开关默认关 / allowlist 空；
④ 不引入锁或并发复用（每调用仍独占 transient worker）。

## 1. 现状证据

- `isolation.py:491-515`：`isolate_tool_call(worker=None)` 自己走完整编排——
  `adapter.spawn` → `IsolationChannel` → `start_reader` → `_execute_on_channel`
  → `if result.timed_out: _confirm_termination` → `finally: adapter.close`。
- `isolation.py:271-309`：`IsolationWorker.call` 已封装同一序列 + 毒化判定
  （`unknown` → 杀+确认 → 毒化 → 关闭），且 `call()` 的毒化检查在入口。
- 两者行为差异：transient timeout → 确认 → 直接返回（一次性，无"后续调用"）；
  resident timeout → 确认 → **毒化**（防迟到结果被下次冒名接收）。
  用 resident 实现 transient 时，毒化只影响"后续调用"——transient 没有后续
  调用，所以毒化无害且多余标记不产生可观察差异（worker 随即 close）。
- `WorkerAdapter` port 与 Win32/Posix 两 adapter 是既成立的两个 adapter，保留。

## 2. 设计

**唯一执行体**：`IsolationWorker`（spawn/通道/执行/确认/毒化/关闭全在它的
`call`/`__exit__`/`close`）。`isolate_tool_call(worker=None)` 收为：

```
with IsolationWorker(adapter=adapter, timeout_s=timeout_s) as w:
    return w.call(tool_name, args, ctx=ctx, approval=approval, timeout_s=timeout_s)
```

**毒化语义不变**：`IsolationWorker.call` 的 `unknown → 确认 → 毒化 → 关闭`
原样保留。transient 场景因 `with` 退出即 close，毒化标记不产生额外行为。

**确认先于释放不变**：`call` 内 `_confirm_termination` 在 `close` 之前（现成）。

**adapter 参数**：`isolate_tool_call(adapter=...)` 透传给 `IsolationWorker(
adapter=...)`（`build_worker_adapter()` 的默认路径不变）。

## 3. 分批与验收（单提交）

| 验收项 | 落点 |
|---|---|
| transient 与 resident 走同一 `IsolationWorker.call` | 代码 diff：transient 分支不再直接 spawn/channel/close |
| 既有隔离 50 用例不退化 | channel/pilot/adapter/platform/context 全绿 |
| 超时确认语义不变 | S2/S4 试点仍 terminated/stopped，wall-clock < 6s |
| 毒化语义不变 | S3b resident 毒化仍拒复用 |
| 层方向/dangling 0；数字同步 | batch_gate 全绿 |

**回滚**：单提交整批回退即恢复当前 transient 编排，零协议/接口变化。

## 4. 风险与回滚

- `with IsolationWorker` 的 `__exit__` 调 `close()`；若 `call()` 内已因
  `unknown` 关闭，`close()` 幂等（`_closed` 标志已存在）——安全。
- transient 的 `adapter.close(handle)` 原在 `finally`；新实现 `with` 退出时也
  在 `finally`（`__exit__`）——语义等价。
- `IsolationWorker.__init__` 的 `supported()` 检查与新 transient 路径一致
  （原 `isolate_tool_call` 也检查）——无行为差异。

## 5. 明确不做

① IsolationResult 字段合并（候选 3）；② per-call id / codec 变更；③ 开关、
allowlist、shell、POSIX 启用；④ 并发复用 worker（每调用仍独占）。

## 6. 实施前确认

本方案来自用户「只做 #4」裁决。确认后按 R-007 单提交实施验证。
