# B2 设计：fold 重建 —— 重启恢复状态（2026-10-30）

> 定稿语义（架构图 + 图上标注速查）：**「重启后从事件流重放算出状态
> （恢复状态，不恢复执行——被打断的 run 标 interrupted，重试靠重提）」**
> `auto_resume=false`。三道门沿用。

---

## 1. 现状勘查：重启丢什么

| 状态 | 现状 | 事件流里可恢复？ |
|---|---|---|
| **turn 计数器**（`controller._turn_counters`） | 内存 dict，重启归零 → turn_id 从 #1 重新编号，**与旧 turn 撞号**（P-4 身份被破坏） | ✅ `turn.admitted` 带 `turn_id={uuid}#n` |
| **session_key → uuid 映射**（`entry_point._sessions`） | 内存 dict，重启后首次提交生成**新 uuid** → 事件流分叉到新 aggregate | ⚠ 需在事件里带 `session_key`（现为 None） |
| **in-flight run** | 重启即消失，无任何标记 | ✅ `run.started` 无 terminal 配对 → 可标 interrupted |
| **mailbox 待办项** | 纯内存，重启即丢 | ❌ 事件只存 label 不存 payload——**按定稿不恢复**（「重试靠重提」） |
| **幂等表**（`entry_point._idempotency`） | 内存 | ❌ 事件不带 idempotency_key——**已知限制**，记录 |

**核心原则**：恢复**状态**，不恢复**执行**。所有恢复物都是「让下一次
提交/重提正确衔接」，不是「把没跑完的接着跑」。

## 2. 设计

### 2.1 `Gateway.recover()` —— 唯一入口

```python
def recover(self) -> dict:
    """从事件流重建内存状态。幂等（重复调用不重复标记）。"""
```

逐 session（`store.list_sessions()`）：

1. **turn 计数**：`turn.admitted` 的 `turn_id` 后缀 `#n` 取 max →
   `controller._turn_counters[session_id] = n`；
2. **session_key 映射**：事件 data 里的 `session_key` →
   `entry_point._sessions[session_key] = aggregate_id`；
3. **interrupted runs**：`run.started` 而 terminal（`run.settled` /
   `run.failed` / `run.interrupted`）缺失的 run_id → 逐个 emit
   `run.interrupted {run_id, reason:"restart"}`（发完即终结，幂等）。

过滤 kinds 只取 4 种事件，大历史 session 也快。

### 2.2 事件契约变化（增量）

- `turn.admitted` data 增加 `session_key`（其余事件不动）——恢复
  `_sessions` 映射的唯一来源。

### 2.3 接线

- `server.py` 宿主：`gw.recover()` → `start_consumer()`（恢复先于消费）；
- 测试/直构不自动调用（与 `start_consumer` 同策略）；
- `auto_resume=false` 兑现：recover 不重建 mailbox、不重提任何工单。

### 2.4 已知限制（记录，不修）

| 限制 | 理由 |
|---|---|
| mailbox 待办不恢复 | 定稿明说「重试靠重提」；payload 不在事件流 |
| 幂等表不恢复 | 事件无 idempotency_key；重启后同 key 重提会再执行一次（语义=新请求） |
| 同 session 重启后 steer 队丢失 | steer 本就是「当前 run 的插话」，run 没了插话无意义 |

## 3. 测试计划（`tests/test_b2_fold.py`）

| # | 用例 | 断言 |
|---|---|---|
| 1 | turn 计数恢复 | 跑 3 turn → 新 Gateway recover → 下一 turn 是 #4 |
| 2 | session_key 映射恢复 | recover 后 `session_id_for(key)` 返回旧 uuid |
| 3 | interrupted 标记 | started 无 settled → recover 后有 `run.interrupted` |
| 4 | 幂等 | recover 两次只发一次 interrupted |
| 5 | 正常收尾不误标 | 全部 settled 的 session 无 interrupted |
| 6 | 无 store（内存模式） | recover() no-op 不抛 |
| 7 | auto_resume=false | recover 后 mailbox 仍空 |
| 8 | 端到端 | 重启 → recover → 提交 → turn_id 续号且事件落同一 aggregate |

## 4. 验收对照

| 门 | 判定 |
|---|---|
| 行为等价 | 不调 recover 的路径零变化；事件只增 `session_key` 字段（增量兼容） |
| 测试不减 | inventory 0 removed |
| 失败集不扩大 | c1_gate 对基线 |
