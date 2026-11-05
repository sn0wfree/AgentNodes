# 技术债修复：R2 引入的三处缺陷（2026-10-30）

> 范围：A1 `install_sink` 无限累积 · A2 `wait_turn` 返旧结果 ·
> A3 `_turn_results` 不清理。全部由 R2 引入，非既有问题。
> 三道门沿用 `stage-c1-acceptance-20261028.md`。

---

## 1. 缺陷与证据

### A1 `install_sink` 无限累积

**代码**（`gateway.py:117-136`）：

```python
def install_sink(self, sink):
    prev = self._external_sink
    def chained(kind, data):
        prev(...)      # 包住前一个
        sink(...)
    self._external_sink = chained     # 每次叠加一层
```

**调用**（`chat.py:918`）：每个 `/chat/send` 请求都调一次。

**后果（比初判更严重）**：

1. **链长线性增长**：第 N 个请求时，每个事件穿过 N 层闭包。
2. **❌ 重复 SSE 事件**：旧 bridge 闭包捕获的是**旧请求**的
   `message_id_holder` / `session_id`。同一 session 第二次请求时，
   旧 bridge 仍然存活并继续推送事件——用旧的 message_id。
   一次 `text_delta` 会被推 2 次以上，前端出现重复文本。

实测：调用 5 次后 `_external_sink` 是 5 层 `chained` 嵌套。

### A2 `wait_turn` 第二次立即返回旧结果

**代码**（`gateway.py:201-212`）：

```python
done = self._turn_done.get(session_id)
if not done.wait(timeout): return None
return self._turn_results.get(session_id)
```

`_turn_done[sid]` 一旦 `set()` **永不 clear**。

**后果**：同一 session 第二次 `?wait=true` 请求，`done.wait()` 立即
返回 True，拿到**上一轮**的 `RunResult`——本轮 turn 可能还没开始跑。

实测：第二次调用耗时 0.000s，返回上一轮答案。

### A3 `_turn_results` 永不清理

每 session 留一条 `RunResult`（含完整 answer 字符串），进程生命周期内
不释放。长跑场景缓慢增长。

---

## 2. 设计

### 2.1 A1：sink 改为「替换 + 可注销」，不是累积

**问题根源**：`install_sink` 语义是「追加」，但调用方要的是「本次请求的
转发器」。累积是错误抽象。

**修法**：改为**线程安全的一次性订阅 + 显式注销**：

```python
def add_sink(self, sink) -> Callable[[], None]:
    """注册外部 sink，返回注销函数（幂等）。"""
    with self._sink_lock:
        self._sinks.add(sink)      # set 而非链
    def unsubscribe():
        with self._sink_lock:
            self._sinks.discard(sink)
    return unsubscribe
```

- 用 `set` + 锁，O(1) 注册/注销，不会嵌套；
- 广播时遍历快照（防并发修改）；
- **保留 `install_sink` 作为 deprecated 别名**（返回注销函数），
  一次性改名会牵动既有测试；
- 调用方（`chat.py`）在同一 turn 结束后注销。

**关键**：注销时机。receipt 路径由 bridge 自身在
`run.answer`/`run.failed`（收尾事件）后自动注销——bridge 知道
「本 turn 结束了」。同步路径在 `finally` 里注销。

为让 bridge 能自我注销，`add_sink` 的注销函数要能传进 bridge 闭包。
做法：先 `unsubscribe = gw.add_sink(bridge)`，bridge 内引用
`holder["unsubscribe"]`（request 作用域可变 dict）。

### 2.2 A2：`wait_turn` 用代际（generation）而非布尔

**修法**：每次提交 turn 递增该 session 的「代际」，`wait_turn` 等
**大于调用时刻代际**的完成：

```python
def wait_turn(self, session_id, *, timeout=None, after=None):
    """等待该 session 在 after 代际之后完成的下一个 turn。"""
```

- `Gateway.submit_*` 路径递增代际（在 `_combined_sink` 收到
  `work.enqueued` 时按 session 记 `gen[sid] += 1`）；
- `wait_turn` 记录调用时的 `gen`，等待 `_turn_gen[sid] > gen`；
- `_consume_once` 完成时 set 该 session 的完成事件，并带上代际。

简化实现（避免过度设计）：用 `Condition` + 每 session 的
`completed_gen`，`wait_turn` 等待 `completed_gen[sid] > start_gen`。

### 2.3 A3：结果留存改为有界

`_turn_results` 改为只保留**最近一次**（本来就是「最近一次」语义），
并随 `completed_gen` 一起更新。不需要跨 turn 历史——`?wait=true`
只要最近一轮。加一个 `_prune` 在超过 N 个 session 时清理最旧。

---

## 3. 测试计划（`tests/test_r7_sink_and_wait.py`）

| # | 用例 | 断言 |
|---|---|---|
| 1 | 连续注册 N 次 sink | 广播次数恰为 N（不叠加）；注销后不再广播 |
| 2 | 注销幂等 | 重复调用 unsubscribe 不报错 |
| 3 | 注销后不泄漏 | 注销全部后内部集合为空 |
| 4 | 同一 session 连续两次 wait_turn | 第二次等的是**新一轮**，不是旧结果 |
| 5 | wait_turn 超时 | 无新 turn 时按 timeout 返回 None |
| 6 | 并发注册/广播 | 无 `RuntimeError: set changed size during iteration` |
| 7 | 既有 chat 契约 | receipt 路径 / wait 路径行为不变 |

## 4. 验收对照

| 门 | 判定 |
|---|---|
| 行为等价 | 单次请求路径字节不变；重复请求从「重复事件」变为「正确单次」——这是修复不是等价 |
| 测试不减 | inventory 0 removed |
| 失败集不扩大 | c1_gate 对基线 |
