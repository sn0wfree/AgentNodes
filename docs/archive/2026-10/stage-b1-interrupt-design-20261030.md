# B1 设计：interrupt 语义 —— 取消当前 run（2026-10-30）

> 计划依据：`docs/archive/2026-10/delivery-summary-r1-r6-20261030.md` §九 · B1。
> 定稿语义：`docs/archive/2026-10/queue-mode-research-20261024.md:29`
> 「interrupt：取消该捕获的活跃 turn，打断输入优先接 next，旧等待输入保序其后」。
> 三道门沿用。

---

## 1. 现状勘查

| 事实 | 证据 |
|---|---|
| `QueueMode.INTERRUPT` 只做**插队**（`appendleft`），不取消正在跑的 turn | `mailbox.py:137` |
| 引擎有**节点间**中断：`GraphInterrupt` + `INTERRUPT_KEY` → router 提前 END | `graph_engine.py:258, 298-303` |
| 但**运行中的节点无法被打断**：`wrapped()` 在 `fn(state)` 里阻塞，没有取消检查点 | `graph_engine.py:292` |
| 新 Gateway 路径**无 cancel API**（旧 SessionService 有 `cancel`/`cancel_session`） | `service.py:291,304`；gateway 无对应 |
| fanout 已有取消：`threading.Event` + `cancel_timeout` + 5s 硬杀 | `graph_engine.py:330, 520` |

**结论**：缺的是三件事——
(1) 引擎接受外部取消信号并在节点边界响应；
(2) 控制器在 interrupt 到达时触发取消；
(3) 取消后 turn 的收尾语义（fenced？settled？下一个 turn 接上）。

## 2. 设计

### 2.1 引擎：`CancelScope`（最小原语）

```python
# core/runtimes/cancel_scope.py
class CancelScope:
    """一次 run 的取消信号。线程安全，只置位不反转。"""
    def cancel(self, *, reason: str = "user") -> None
    @property
    def cancelled(self) -> bool
    @property
    def reason(self) -> str | None
    def raise_if_cancelled(self) -> None   # 抛 RunCancelled
```

- 注入路径：`state["cancel_scope"]`（与 `steer_inbox` 同型，散装 state 键）；
- `GraphEngine._wrap` 在 **节点开始前** 和 **节点结束后** 各检查一次
  （`raise_if_cancelled`）——这是「安全点」语义，与 steer 一致；
- 节点内部（LLM 调用）不强制打断：那是 provider SDK 的事，强杀会破坏
  连接。**节点边界取消**已满足「打断输入优先接 next」的体验要求。

### 2.2 控制器：interrupt 到达 → 取消活跃 run

```
Mailbox.submit(INTERRUPT) 时若该 session 有活跃 run：
  → 通过注册表拿到该 run 的 CancelScope → scope.cancel("interrupt")
  → 中断项照常入队（已有行为：appendleft）
```

- 控制器在 `_execute` 里建 `CancelScope`，注册到 Gateway 的
  `_active_scopes[session_id]`；`finally` 注销；
- **不新增全局单例**：用 Gateway 上已有的 per-session 注册表
  （`_turn_gen` 同处），与 fence 域一致（物理 session id）。

### 2.3 取消后的收尾语义

| 情形 | 行为 |
|---|---|
| 节点边界检测到取消 | 引擎返回 `{INTERRUPT_KEY: {...}}`（已有机制） |
| 控制器收到中断结果 | 发 `run.cancelled` 事件；**照常 `validate` fence**（取消的 run 不应被后续 run 误判为已提交，但也不该写结果）；`run.settled{ok:False, cancelled:True}` |
| 队列 | interrupt 项已在队首，下一个 turn 立即接上（已有语义） |
| fence | 取消的 run 释放栅栏（它死了），后续 turn 可正常 claim |

**关键决定**：取消的 run **不产生 `run.answer`**（无产物），
只发 `run.cancelled` + `run.settled{ok:False,cancelled:True}`。
这让「陈旧答案不落库」对中断同样成立。

### 2.4 对外 API（Gateway 路径）

```python
Gateway.cancel_session(session_id, *, reason="user") -> bool
```

- 有活跃 run → 置取消信号，返回 True；
- 无活跃 run → 返回 False（不报错，幂等）；
- `/chat/cancel` 在新路径上接它（旧 SessionService 路径不动，随 P5-1c 一起死）。

### 2.5 明确不做

- **强制杀线程**：Python 无法安全杀线程；节点边界取消是正确做法；
- **级联取消子 run 树**：fanout 已有取消（`threading.Event` + 硬杀），
  cpu 节点不存在，所以「子 run 树」当前只有 fanout 一层——已有覆盖。
  重开条件=出现 graph 子图节点时；
- LLM 请求中途 abort：不碰 provider SDK。

## 3. 测试计划（`tests/test_b8_interrupt.py`）

| # | 用例 | 断言 |
|---|---|---|
| 1 | CancelScope 基本语义 | cancel 后 cancelled=True，raise_if_cancelled 抛 |
| 2 | 引擎节点边界响应取消 | 第 1 节点后取消 → 第 2 节点不执行，返回 INTERRUPT_KEY |
| 3 | 取消不发 run.answer | 事件流只有 run.cancelled + settled{cancelled} |
| 4 | interrupt 工单触发取消 | 活跃 turn 进行中提交 INTERRUPT → 该 turn 被取消 |
| 5 | 取消后下一 turn 接上 | interrupt 项成为下一个 turn 并正常完成 |
| 6 | fence 正确释放 | 取消后 active_run 为空，后续 turn 可 claim |
| 7 | cancel_session 无活跃 run | 返回 False，不抛 |
| 8 | 幂等取消 | 连续两次 cancel 不报错 |

## 4. 验收对照

| 门 | 判定 |
|---|---|
| 行为等价 | 无取消场景逐字节不变；新增事件仅在被取消时出现 |
| 测试不减 | inventory 0 removed |
| 失败集不扩大 | c1_gate 对基线 |
