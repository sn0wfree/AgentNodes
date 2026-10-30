# B3 评估：7 阶段 / 6 真值控制器 —— 勘查后收窄为一项（2026-10-30）

> 计划依据：交付总结 §九。原估 3-5d「7 阶段/6 真值控制器」。
> **勘查结论：6 真值是从未定义过的死概念；7 阶段与现有事件生命周期
> 一一同构，独立实现是纯簿记。真正缺的只有一件事：非阻塞的状态查询。**
> 三道门沿用。

---

## 1. 定义考古

| 概念 | 文档出处 | 实际定义 |
|---|---|---|
| **7 阶段状态机** | `industry-benchmark-openclaw-20261008.md`（10-08 对标） | `idle / admitting / preparing / running / finishing / settling / mutating`（OpenClaw session controller 的生命周期） |
| **6 正交真值** | `unified-entry-design.md:108` 仅一行：`status: RunStatus # 6 个正交真值` | **从未定义**。`RunStatus` 枚举值在任何文档中不存在；该设计所属的「统一入口 RunHandle」形态已被 v6（入口塌进 Gateway）取代 |

## 2. 现状对照

### 7 阶段 vs 现有事件生命周期

| 设计阶段 | 现有对应 | 状态 |
|---|---|---|
| idle | 无活跃 run（`fence.active_run` 为空） | ✅ 可查 |
| admitting | `turn.admitted` 事件 | ✅ |
| preparing | `_execute` 的装配段（engine/context 注入） | ✅（区间内，无独立事件） |
| running | `run.started` → `run.settled` 之间 | ✅ |
| finishing | fence validate + answer 产出段 | ✅（区间内） |
| settling | `run.settled` / `run.cancelled` / `run.fenced` | ✅ |
| mutating | 落库（事件→projector，在消费侧完成） | ✅（归属数据面） |

**判断**：7 个阶段与现有事件**一一对应**。控制器单线程串行（R3 串行域），
阶段机的价值本在「管理并发状态转换」——这里不存在并发。独立实现
7 状态枚举 + 转换校验是**纯簿记，零行为收益**。

### 6 真值

从未定义。它所属的「RunHandle + WorkEntry 协议」形态已被 v6 取代。
**判定：死概念，随评估文档记录后关闭。**

## 3. 真实缺口：非阻塞状态查询

receipt 契约（E1=C）说「submit 即回、答案走 SSE、可 poll 状态」——但
现在**唯一的查询原语是 `wait_turn`（阻塞）**。断线重连、轮询型客户端、
`?wait=true` 超时后的补救查询，都需要一个不阻塞的快照。

### 设计：`Gateway.session_status(session_key)`

```python
@dataclass(frozen=True)
class SessionStatus:
    session_key: str
    session_id: str        # EntryPoint uuid（未注册则空）
    active: bool           # 有活跃 run（fence）
    active_run_id: str     # 活跃 run（无则空）
    last_turn_id: str      # 最近完成的 turn（无则空）
    last_ok: bool | None   # 最近完成的结果（None=尚无）
    last_answer: str       # 最近答案（receipt 路径下是已落库的）
```

- 全部从**已有内存状态**读（fence / _cancel_scopes / _turn_results /
  _turn_gen），无新状态机、无 IO；
- 与 B2 recover 兼容：重启后 last_* 为空、active=False——诚实反映
  「恢复状态不恢复执行」。

## 4. 明确不做

| 项 | 理由 |
|---|---|
| 7 状态枚举 + 转换校验 | 与事件一一对应；单线程串行下无并发可管理 |
| RunStatus 枚举「补定义」 | 死概念；强行定义 6 个值是给已废弃的 API 形态补遗嘱 |
| preparing/finishing 独立事件 | SSE 已有 node.started/completed 细粒度；再加一层只有控制器自己消费 |

## 5. 测试计划（`tests/test_b3_status.py`）

| # | 用例 | 断言 |
|---|---|---|
| 1 | 未知 session | active=False，全空字段 |
| 2 | 提交后未消费 | active=False（队列里，没开跑）——但 mailbox 深度可见 |
| 3 | 活跃中 | active=True + active_run_id |
| 4 | 完成后 | last_turn_id/last_ok/last_answer 正确；active=False |
| 5 | 取消后 | last_ok=False；cancelled 结果可见 |

## 6. 对架构图的影响

| 格 | 原标注 | 新标注 |
|---|---|---|
| SessionController「7 阶段 · 6 真值」 | ⏳ | **⏹ 判定不做**（7 阶段=现有事件同构；6 真值=从未定义）+ **✅ session_status 查询**（本刀） |
