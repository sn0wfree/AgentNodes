# 编排层三件套：控制面 / 策略 / 环境 的关系

> 关联：`architecture-v3-openclaw-inspired.md`（总纲）· `architecture-diagram.html`（v4 图）· `refactor-master-plan-20261008.md`（主线）
> 证据：`scripts/triad_probe.py`（现状归属实测）· `core/study/phase_engine.py`（BranchContext / RoundOutcome 契约）

---

## 0. 结论

**三者不是并列的三个子系统，而是同一条链上的三个环节 + 一个贯穿的环境。**

```
SessionController   时序/所有权   谁下一个跑、能不能开这一轮
      │ 准入判定
      ├──────────────────────────────────────────────┐
      │ 询问意图                                     │ 创建
      ▼                                              ▼
Director            语义/意图      这一轮跑什么        RunContext  资源/环境
      │ 产出 intent（= BranchContext）                 │ 在什么约束下跑
      ▼                                              │
Runtime             执行/算法      怎么做（ReAct / 跑一轮 / DAG）
```

**一句话**：控制面决定**谁与何时**，策略决定**做什么**，环境决定**在什么约束下做**；Runtime 只认「意图 + 环境」，不认控制面、不认策略。

---

## 1. 为什么要拆成这三格（现状证据）

现状是**三处各有一套准入、三处各有一套环境**：

| 关注点 | 现状实现（实测） | 问题 |
|---|---|---|
| **准入（谁跑）** | `SessionService._session_queues`（asyncio.Queue）· `StudyScheduler` 全局+每用户 semaphore · `StudyRunner._top_control_check` + `ControlToken.paused` | **三套准入语义**，各自判停 |
| **意图（跑什么）** | `phase_engine._prepare_round_preamble` → `BranchContext`（objective / previous_summary / directive_text / current_state / graph …） | 意图构造**埋在引擎内部**，不可替换 |
| **环境（预算/事件/追踪/取消）** | `budget.py`（118 行，runner 侧记账 + loop 侧 `_budget_tokens`）· `loop._emit` / `_trace_writer` · `ControlToken` | **预算两套**，事件/追踪各写各的 |

→ 三格拆分的意义不是「好看」，是让**每一格只有一个 owner**，从而可以分别替换、分别测试。

---

## 2. 三个关键的结构决定

### 决定 A · RunContext 不是平级的第三格，而是**准入的产物**

RunContext（预算余量 / 事件出口 / 追踪 / 取消令牌 / 快照句柄）**由 SessionController 在准入通过时创建**，随 run 下发给 Runtime。

**理由**：若 RunContext 是独立子系统，它会像 ContextHub 一样膨胀成上帝对象（我们刚批评过 OpenClaw 之外的「什么都往中枢塞」）。做成「准入的产物」，它**只能包含「本轮的环境」，不可能包含「做什么」**。

**推论**：RunContext 是 **per-run** 的，不是全局单例；跨 run 的账（总预算、并发容量）留在 Controller。

### 决定 B · Director 的产出**已经有契约**：`BranchContext`

现状 `BranchContext` 已经是「一轮要做什么」的完整载体（objective / previous_summary / directive_text / current_state / run 路径 / graph）。
→ **Director 不是新概念**，它是把 `BranchContext` 的构造从 `phase_engine` 内部**提到编排层**，并允许被替换：

| intent 来源 | 现状 | 目标 |
|---|---|---|
| 人给（guidance.md + directive） | ✅ 已有 | 保留（人保留意图权） |
| 规则给（模板/预设） | 部分 | 保留 |
| **agent 自主 propose** | ❌ 无 | Director 的新能力（切 1.1 / C2） |

**红线**：Director **能否决**（它决定下一轮做什么 = 改控制流）——这与 hook 相反，必须写进 `extension-points.md`：
- **hook：只读，不得改控制流**（已立）
- **Director：可决，是唯一的内容级否决者**
- **Runtime：不得回调 Controller**（防双向耦合）

### 决定 C · 「停止」分三层，预算拆两半

现状：9 类 `ShutdownReason` 在 runner；三维预算横跨 runner 与 loop。混在一起会导致「谁该停」的争议。

| 层 | 裁决点 | 问题 | 现状归属 |
|---|---|---|---|
| **准入** | Controller | 值不值得**开**这一轮？（剩余预算>0？并发槽位有？没被取消？） | 三处分散 → 收敛 |
| **执行** | RunContext + Runtime | 这一轮跑到一半会不会**超**？ | budget.py + loop 两侧 → 合并 |
| **内容** | Controller + Director | 要不要**再来**一轮？（达标 / 停滞 / 丢弃） | `_check_stop_conditions` 9 类 → 保留在此 |

**预算拆两半**：
- **准入预算**（Controller 持有总账）：这轮能不能开
- **执行预算**（RunContext 持有本轮余量）：这轮能花多少

---

## 3. 边界（禁止项）——防止重新长出耦合

| 禁止 | 理由 |
|---|---|
| Runtime 不得回调 Controller | 双向依赖 = 回到今天的乱局 |
| Director 不得读 RunContext 的预算 | 预算是**准入条件**，不是策略输入；策略看预算就会「贴着上限跑」 |
| RunContext 不得含「做什么」 | 那是 intent；混进来它就变成第二个 ContextHub |
| Controller 不得直接拼 messages | 那是 ContextHub 的活；Controller 只管「准不准跑」 |
| Director 不得自己开 run | 它只产出意图；开不开是 Controller 的准入权 |
| 不变量层不得插件化 | 预算/停止/状态机/scope 由结构冻结 |

---

## 4. 现状 → 目标 的逐项归属

| 现状实现 | 目标归属 | 刀 |
|---|---|---|
| `SessionService._session_queues` + `create_task` | Controller（准入） | A2 |
| `StudyScheduler` 全局/每用户 semaphore | Controller（容量） | C5（单一调度器） |
| `StudyRunner._top_control_check` + `ControlToken` | Controller（取消/暂停） | A2 |
| `StudyRunner._check_stop_conditions`（9 类停因） | Controller（内容停止） | A2 |
| `phase_engine._prepare_round_preamble` → `BranchContext` | **Director（意图）** | C2 |
| `budget.py`（118 行） | RunContext（执行预算）+ Controller（准入预算） | A4 / B2 |
| `loop._emit` / `_trace_writer` | RunContext（事件/追踪出口） | A4 |
| `AgentLoop` / `phase_engine` / `swarm` | Runtime（A1 / B1） | A1 / B1 |
| `ContextHub`（计划） | 独立于三者：它服务 **Runtime**，不是 Controller 的 | A4 |

---

## 5. 与「自主性」的关系（这一格最难，也最关键）

Director 是**自主性的入口**，也是**唯一可能自我发奖状**的地方。

| 阶段 | Director 的职责 | 自主程度 |
|---|---|---|
| 今天 | 意图 = 人的 directive + 模板 | 人驱动 |
| B1 | Director 可替换，但默认仍跟随人的 directive | 人驱动 + 可插拔 |
| C2 | Director 自主 propose（propose_next + 派生假设 + 停因→动作映射） | **半自主** |
| D2（RSI） | Tier 1 工件提案 | 自我改进 |

**护栏（不可省）**：
1. Director 的自主 proposal 必须过 **不变量层**（如「必须有证据支撑的假设」）
2. 评测侧必须有 **holdout**（否则 Director 会优化可见的 70 题）
3. 人的 directive 始终是**最高优先级 intent**（Director 只能在人的边界内自主）

---

## 6. 一句话

> 控制面是**裁判**（谁、何时、能不能开），策略是**教练**（这一轮跑什么），环境是**场地**（在什么预算/事件/取消约束下跑），Runtime 是**运动员**（怎么跑）。
>
> 四者只允许单向协作：**裁判定场次 → 教练定战术 → 场地给约束 → 运动员执行**；
> 运动员不能找裁判加时，教练不能偷看场地预算表——**否则「一个 owner 对一件事」立刻退化成「谁都能干所有事」**。
