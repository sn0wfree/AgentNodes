# R1 设计：fence 挂根 + chat 驱动点勘查（2026-10-30）

> 计划依据：`docs/roadmap-next-20261030.md` R1。三道验收门沿用
> `docs/stage-c1-acceptance-20261028.md`（行为等价 / 测试不减 / 失败集不扩大）。
> 决策记录：D1=fence 接线 ✅（用户「开始吧」采纳建议）；D2=延后分库+仅接 P-3；
> D3=P5-1c 以「R2 上线一周无回归」为触发；D4=历史失败随手清。

---

## 1. 目标

把 `SessionFence`（已建、零调用方）接进生产写路径，使 INV-4
「产物落库必过 fence」从文档承诺变成结构事实——这是 R3.2 study 收编
（round 提交过栅栏）的前置。

## 2. 现状勘查

| 事实 | 证据 |
|---|---|
| fence API 齐全：`claim(session, run)` 认领即超越 / `validate(token)` 提交校验 / `release(token)` 收尾（仅 active 可释放） | `core/gateway/fencing.py:55-78` |
| Gateway 构造了实例但从不使用 | `core/gateway/gateway.py:41` `self.fence = SessionFence()` 后无引用 |
| SessionController 完全不知道 fence | `session_controller.py` 无 fence 字样 |
| 不变量表里只有 INV-5（认领即超越，自检自己的数学），INV-4（产物必过 fence）**不在注册表**——因为没有可检查的对象 | `invariants.py:113-119` |

## 3. R1.2 勘查：chat 生产驱动点（只勘查，不动）

入口：`api/routers/chat.py:684` `_send_via_gateway`，五步：

1. **读历史**（chat.py:707-719）：`SessionStore.get_messages` + 旧路径同一转换函数；
2. **提交**（:727）：`gw.entry.submit(api_chat_request(...))`，拒绝→400；
3. **执行**（:737）：`await run_in_executor(None, lambda: gw.run_once({WorkKind.CHAT}))`
   ——**同步驱动在请求路径内**，HTTP 请求等整个 turn 跑完；
4. **落库**（:756）：事件 `message_received` / `assistant_message` → projector；
5. **SSE**（:774）：`agent_done`。

三个结论（R2.1 的设计输入）：

- **receipt 不是早返回**：submit 与执行之间没有语义缝隙，mailbox 的排队
  / 串行 / cap 语义对 chat 用户目前不可见；
- **驱动是「谁请求谁驱动」**：第二个并发请求会撞上同一个 mailbox
  （`Mailbox` 无锁，`_evict_expired_locked` 的命名暗示单线程假设），
  现状靠「没人并发」侥幸；
- **无常驻消费者**：`session_controller.py` 头注自述「A2 换成后台消费者
  常驻」未做。R2.1 的正确形状 = Gateway 内常驻 worker 消费 mailbox，
  请求只 submit。

## 4. 设计：fence 挂根

### 4.1 接线（选项 A 同型：组装根注入）

```
Gateway.__init__            SessionController.__init__
  self.fence = SessionFence() → fence: SessionFence | None = None（新参数）
  SessionController(..., fence=self.fence)     None = 不设防（测试/脚本路径，
                                               与 permission_gateway 同型先例）
```

- Gateway 是唯一组装根 → 生产恒有 fence；直构 controller 的既有测试零改动；
- `verify_invariants()`（gateway.py:119）检查的实例与控制器用的实例同一，
  不出现双栅栏。

### 4.2 `_execute` 三触点（顺序关键）

```
run_id 计算
  ↓
① claim：token = fence.claim(session_id, run_id)      # 认领即超越（INV-5）
  ↓
engine.run(state)
  ↓ 成功
② validate(token)                                     # INV-4：落库前校验
  ├─ ok    → ③ release(token) → emit run.settled{ok:True}
  └─ deny  → emit run.fenced{run_id, reason} → emit run.settled{ok:False, fenced:True}
             → RunResult(ok=False, error="fenced: ...")；不 release（后继者持有）
  ↓ 异常路径（引擎崩）
现有逻辑不变（run.failed + run.settled{ok:False}）；release 与否：
  仍 active 则 release（本 run 死了，不该挡住下一个 turn）
```

关键顺序约束：**validate 必须先于 `run.settled{ok:True}`**——事件即事实，
settled 事件本身就是「产物成立」的声明，它必须过栅栏。

### 4.3 并发语义（诚实版）

今天**不存在真并发**：mailbox 无锁、控制器单循环消费、fence.validate 在
顺序执行下恒 pass。本次接线的价值是结构性的：

- INV-4 成为可检查的事实（§4.5），而非文档；
- 为 R2.2（steer/interrupt）、R3.2（study round 与 chat 抢 home session）
  预先立好规矩：**后到超越、先到的提交被拦**——这正是 fence 的设计语义，
  不是新发明；
- 两条同 session 并发 HTTP 请求（现状隐患）从「可能交错双双成功」变为
  「后到者正常，先到者收到 502+fenced」——方向正确，但根治靠 R2.1
  单消费者，不在本刀承诺。

### 4.4 INV-4 落地：行为检查进注册表

```python
def _inv_settle_passes_fence() -> bool:
    """INV-4：settle 前必 validate；被超越的 run 拿不到 ok=True。"""
    # 真 SessionController + 真 SessionFence + stub engine（run 中途
    # 用新 run_id 超越同一 session）→ 断言 RunResult.ok is False
    # 且事件流含 run.fenced。
```

放进 `_REGISTRY`：`Invariant("INV-4", "产物落库必过 fence（settle 前校验）", ..., "R1")`。
这是行为检查不是源码 grep——控制器是唯一落库发起方，检查它即可。

### 4.5 事件契约

| 事件 | 时机 | 字段 |
|---|---|---|
| `run.fenced` | validate deny | `run_id, session_id, reason` |
| `run.settled` | fenced 时 | `ok:False, fenced:True`（新增可选键，向后兼容——消费方按 ok 判断） |

## 5. 测试计划（`tests/test_r1_fence_wiring.py`）

| # | 用例 | 断言 |
|---|---|---|
| 1 | 正常轮：有 fence | ok=True；settled 前 fence 已 release（active_run→None） |
| 2 | 中途超越：stub engine 在 run 内以新 run_id 认领同 session | ok=False；事件含 run.fenced + settled{ok:False,fenced:True}；答案不返回 |
| 3 | 无 fence（None） | 行为与现在逐字节一致（等价门） |
| 4 | Gateway 注入同一性 | `gw.controller._fence is gw.fence` |
| 5 | 连续两 turn | 第二 turn 正常 claim（seq 单调递增，release 不误伤） |
| 6 | 引擎崩 + fence | run.failed 路径不变，fence 释放，下一 turn 可跑 |
| 7 | INV-4 | `verify_all()` 全绿含新不变量 |

## 6. 本刀不做

- interrupt 级联取消（R2.2/R5）；steer 安全点（R2.2）；
- mailbox 加锁（并发根治=R2.1 单消费者，不是补丁锁）；
- 事件契约变更以外的 API 变化（`RunResult` 不加字段，error 字符串承载原因）。

## 7. 验收对照

| 门 | 判定 |
|---|---|
| 行为等价 | 无 fence 路径（直构 controller）零改动；有 fence 顺序路径行为不变（validate 恒 pass） |
| 测试不减 | 新增 7 用例；`test_session_controller` / `test_gateway_*` / `test_invariants` 全量照跑 |
| 失败集不扩大 | `c1_gate.py` 对冻结基线，node-id 集合只许缩小 |
