# 上下文中枢的成员边界 — 「所有都接入」是否合理

> 关联：`context-hub-discussion.md`（中枢功能）· `plug-in-contract-design.md`（接缝三层）· `refactor-master-plan-20261008.md`（主线）
> 问题：**「把上下文管理作为中枢，所有部件都接入」这个设计是否合理？**

---

## 0. 判定

| 命题 | 判定 |
|---|---|
| **上下文作为中枢** | ✅ **合理**——它是「影响模型输入」的唯一汇聚点 |
| **所有部件都接入中枢** | ❌ **不合理**——会重演「core→api 反向依赖」与「injector 藏业务」两宗旧病 |

**正确表述**：

> **「所有影响模型输入的东西」都走中枢；「不影响模型输入的东西」不接入。**

这一句是成员判定规则的全部。其余都是它的推论。

---

## 1. 为什么「全接入」会翻车（三个具体机制）

| 机制 | 后果 | 我们已付过的学费 |
|---|---|---|
| **中枢成为 import 中心** | 中枢要 import 每个生产方 → 生产方反向依赖中枢 → **依赖倒置重演** | `event_store → projector`（3 条 core→api 边） |
| **中枢承担非上下文职责** | 塞进传输/执行/持久化/控制流 → 不可测、不可换、每次调用都要跑一遍不相关代码 | `compact.py` 832 行已经证明了「上下文相关代码会自我膨胀」 |
| **provider 变成 service 藏身处** | 业务逻辑（查库、算因子、跑回测）都塞进 `provide()` → provider 变成「万能服务」 | 现在的 5 个 injector + 11 个 store 的角色混淆 |

**判别信号**：一个部件如果**不需要出现在模型输入里**，它接入中枢就是纯负担。

---

## 2. 成员判定规则（可执行）

```
问自己一个问题：这个部件会改变「模型这次看到什么」吗？
  会  →  接入中枢（作为 ContextProvider 或 Renderer）
  不会 →  不接入（它可能是中枢的消费者，或另一个中枢的供给方）
```

配套两条排除项：

- **不 import 生产方**：中枢只依赖协议；生产方注册进来
- **不承接业务**：provider 只「取数据 + 标注元数据」，不做业务计算

---

## 3. 本仓库的逐项归属（用真实部件核对）

| 部件 | 是否改变模型输入 | 归属 | 接入方式 |
|---|---|---|---|
| 5 个 injector（goal/todo/memory/continuation） | ✅ | **中枢·贡献方** | 适配为 `ContextProvider` |
| `PromptBuilder`（4 实现） | ✅ | **中枢·渲染器** | 已是协议，直接用 |
| toolset / 44 个工具的 schema | ✅ | **中枢·工具区** | 现由 `assemble_loop_parts` 拼（还越界读了 `registry._tools` 私有属性 → 正好归位） |
| 记忆库 / hypothesis / goal / knowledge | ✅（检索结果进 prompt） | **经 provider 间接接入** | 库本身**不接入** |
| 历史消息 + 工具结果 | ✅ | **中枢·历史区** | 内建 |
| `inject_post_response`（返回 bool） | ❌ 是控制决策 | **不接入** | 留在 loop/step |
| LLM 客户端 / provider adapter | ❌ 是消费者 | **不接入** | 中枢产出 bundle 后交给它 |
| 工具**执行** | ❌ 是执行面 | **不接入** | 执行结果才作为 item 进上下文 |
| 权限闸门（permission） | ❌ 是闸门 | **不接入** | 拒绝信息可作为 item，但不承载闸门逻辑 |
| 预算 / 停止策略 | ❌ 是策略输入 | **作为仲裁参数**，不是贡献方 | 中枢消费 `budget.py` |
| `EventStore` / SSE | ❌ 是事实流 | **不接入**（另一个中枢） | 只有「把事件摘要成上下文」时才经 provider |
| study / swarm / workflow 编排 | ❌ 是消费者 | **不接入** | 它们消费 bundle |
| 11 个 store | ❌ 是持久化 | **不接入**，经 provider | 避免 store 直接进中枢 |
| MCP server（协议层） | ❌ | **不接入**；其工具经 plugin 接入 | |
| AgentLoop 循环本体 | ❌ | **不接入**；它调用中枢 | |

**统计**：真正直接接入中枢的只有 **4 类**（贡献方 / 渲染器 / 工具区 / 历史区），其余 10+ 类明确不接入。**「所有都接入」= 把 4 类扩成 15 类。**

---

## 4. 那「一个门」的诉求怎么满足？

用户真正想要的可能是：**「一个地方能看到全部输入」**（可观测、可回放、可评测）。这个诉求合理，但解法不是「都接入」，而是：

### 4.1 统一门面，不统一实现

```
                    ┌──────────── CapabilityRegistry（能力目录：有什么）
                    │
  供给方 ──注册──▶ │   ┌──────────── ContextHub（工作集：模型看到什么）
  (provider)       │   │  ① 收集 ② 仲裁 ③ 渲染 ④ explain/snapshot
                    │   └──────┬─────���────┐
                    │          │           │
                    │      Consumer      Consumer
                    │      (LLM/loop)   (编排/评测)
                    │
                    └──────────── EventStore（事实流：发生了什么）
```

- **三个中枢各有职责**，可共用一个门面（`describe()` 一份清单）
- 一次调用产生的 **`ContextSnapshot` 同时记录**：内容（bundle）+ 用到的 provider + 可用的能力清单 + 关联事件 id
- 于是「一个地方看全部」达成，**但实现没有被塞进一个对象**

### 4.2 快照要包含「没进上下文但存在的东西」

```json
{
  "query": {"task": "...", "session_id": "...", "iteration": 3},
  "items": [{"kind": "hypothesis", "source": "hypothesis_store:42", "created_at": "...", "confidence": 0.8, "tokens": 120}],
  "dropped": [{"kind": "tool_result", "tokens": 900, "reason": "budget"}],
  "providers_used": ["goal", "memory", "hypothesis"],
  "capabilities_available": {"tools": ["run_backtest", "compute_factor"], "toolsets": ["researcher"]},
  "budget": {"limit": 32000, "used": 28100},
  "events_ref": ["evt_123", "evt_124"]
}
```

**要点**：`providers_used` / `capabilities_available` / `events_ref` 让**未接入中枢的部件也出现在快照里**——可观测性达成，依赖不倒置。

---

## 5. 中枢的最终职责清单（收敛后）

| # | 职责 | 明确排除 |
|---|---|---|
| 1 | 收集（问 provider 要 items） | 不自己去查库、不跑工具 |
| 2 | 仲裁（唯一 token 账本 + 裁剪 + scope 隔离） | 不定业务优先级（由 provider 的 priority 声明） |
| 3 | 渲染（bundle → messages） | 不硬编码 prompt 模板（用 `PromptBuilder` 协议） |
| 4 | 溯源与快照（explain / snapshot） | 不做回放执行（那是 eval 的事） |
| 5 | 记录（用了哪些 provider / 能力 / 事件） | 不代理事件总线 |

**中枢不做什么**（= 一句话的背面）：

- 不做传输（LLM 调用、SSE 归上层）
- 不做执行（工具归 toolset 层）
- 不做持久化（归 store 层）
- 不做控制流（归 step / policy 层）
- 不做业务（归 provider 背后的 service 层）

---

## 6. 对主线的修订（无新增刀，只加约束）

| 变化 | 内容 |
|---|---|
| **0.8 中枢试点** | 增加约束：试点 provider **只读 goal 快照**，不引入任何业务逻辑；试点目的是验证「provider→bundle→render」通路 |
| **1.7 injector→provider** | 只迁 3 个注入器（goal/todo/memory）；`inject_post_response` **明确不迁**（是控制决策） |
| **0.6 统一注册** | 与中枢的关系写清：Registry 是「供件目录」，Hub 是「工作集」；Hub 通过 Registry **解析** provider，不直接 import |
| **新增门禁** | CI 检查：`core/agent/context_hub.py` 不得 import 任何 provider 实现（只允许协议）——**防止中枢长成上帝对象** |

**度量新增**：
| 度量 | 目标 |
|---|---|
| 中枢直接接入的部件类别数 | **≤ 5**（超过即说明有非上下文职责混入） |
| 中枢 import 的生产方实现数 | **0**（门禁） |
| 快照覆盖的部件类别 | 全部（经 `providers_used` / `capabilities_available` / `events_ref` 标注） |

---

## 7. 一句话

> **「上下文作中枢」是对的；「所有都接入」是错的。**
> 正确的规则只有一条：**会改变模型输入的，才接中枢。**
> 想要「一个地方看全部」，用**统一快照**（含未接入部件的标注）实现，不要用**统一依赖**去实现——
> 否则我们会把刚刚花两轮评审清掉的反向依赖，再从中枢方向请回来。

---

## 8. 成本收益判定（这个设计「更灵活、更轻」吗？）

| 维度 | 判定 | 依据 |
|---|---|---|
| **更灵活？** | ✅ **是，且是有界的灵活** | 新增上下文来源 = 写 1 个 provider + 1 行注册；换渲染 = 换 `PromptBuilder` 实现；换记忆后端 = 换 provider 内部。**不接入的部件（执行/编排/权限/传输）不受影响，也不需要这种灵活** |
| **持续维护更轻？** | ✅ **是** | 一个硬问题（预算+溯源+快照）只在一处解决；provider 只管取数与标注 |
| **一次性建设更轻？** | ❌ **否，更重** | 新增 6 个概念（Item/Provider/Query/Bundle/Hub/Snapshot）+ 中枢多一跳 + 约 **4.5 天**（0.8 1.5d + 1.7 1.5d + 2.6 1.5d） |
| **双轨期风险** | ⚠️ **真实存在** | 迁移期「旧 injector + 新 provider」并存 → 与我们吃过的苦（dag/env、memory API、消息投影双轨）同类，必须限期收口 |

### 净判断（决策用）

> **如果 W1（自主性：记忆闭环 / 假设工具 / propose_next）要做，Hub 的边际成本几乎为零**——它就是 W1 所需的「注入标准化」；不做 Hub，W1 会在 injector 上继续堆特例。
> **如果只做 G1（上下文不超预算）而不做 W1，Hub 属于过度设计**——只做 `ContextPlan`（2–3 天）即可。

### 什么时候**不要**建 Hub

| 条件 | 结论 |
|---|---|
| 不打算做 `ContextSnapshot` 与 eval 上下文 diff | **不建**——那只是给 3 个注入器套一层 |
| 不打算增加/替换上下文来源（记忆后端、假设源、知识源） | **不建**——provider 协议无摊销对象 |
| 只想要「预算不超」 | **只做 G1 ContextPlan** |

### 验证假设（这才是 0.8 试点的真正目的）

试点不是「先做个壳」，而是**用 1.5 天验证两个假设**：

1. **通路可行**：provider → bundle → render 能否在不破坏现有行为的前提下跑通？
2. **摊销成立**：provider 化之后，**新增一个上下文来源（如第二个记忆后端）是否真的从「读源码模仿」降到「写类 + 一行」**？

若 2 周内观察不到「新增来源变快」的实例，说明摊销不成立 → **砍掉 Hub，退回纯 G1 ContextPlan**（G1 独立成立，不受影响）。

### 新增风险与对策

| 风险 | 对策 |
|---|---|
| provider 变成新 service 藏身处 | CI 门禁：provider 只允许「取数 + 标注」，禁业务计算（review 检查点） |
| 迁移双轨漂移 | 1.7 与旧路径**同刀切换**（不留并行期）；或旧路径加 `@deprecated` 并设删除日期 |
| 抽象税（学不动） | 只暴露 3 个概念给实现者：`ContextItem` / `ContextProvider` / `ContextQuery`；`Bundle`/`Hub` 是内部实现 |
| 中枢长胖 | 度量：直接接入类别 ≤ 5；import 生产方实现 = 0 |
