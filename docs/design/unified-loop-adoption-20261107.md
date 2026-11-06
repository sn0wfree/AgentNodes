# UnifiedAgentLoop 设计借鉴评估（v1.0 · 2026-11-07）

> **来源**：用户（llmwikify 作者）提出把 llmwikify 的 UnifiedAgentLoop
> 设计借鉴到 AgentNodes，并已拷贝部分代码到
> `src/agentnodes/kernel/agent/unified/`。
>
> **本文回答**：这套设计与 AgentNodes 现有 loop 的真实差距、
> 哪些值得要、哪些已有、怎么接、放哪。
>
> **先纠正一个事实**：拷贝过来的 `unified/` 是 llmwikify 的
> **backward-compat shim 包**（`loop.py` 只有 13 行 re-export）。
> 设计本体——301 行骨架、`_core_types.py`、`steps/`——在
> **llmwikify 的 `kernel/agent/`** 里，没有被拷过来。

---

## §0 结论摘要

**值得借鉴，但不是替换，是收敛。**

关键事实：**AgentNodes 已经有同构的 strategy 抽象**
（`strategy/protocol.py` 定义了 9 个 Step 协议 + `strategy/steps/` 9 个实现
+ `LoopStrategy` 数据类），llmwikify 设计的核心思想——模板方法 + 策略注入
——两边一致。**真正的差距不在"有没有策略层"，而在四件事**：

| # | llmwikify 的做法 | AgentNodes 现状 | 差距实质 |
|---|---|---|---|
| 1 | 骨架 **301 行独立类**，策略/预算/追踪全注入 | `_run_loop_core` 埋在 **1283 行**的 loop.py 里，与预算/git 提交/假设自动创建/goal 注入混在一起 | **骨架未隔离** |
| 2 | **`deciders: dict{位置→handler}`**——三个检查点显式可插拔 | 停止判断分散在 `StopStep`/`ContinuationStep` 固定槽位 + loop 内部 if | 「在哪停」不是配置 |
| 3 | **双 handler 接口**：`StepHandler`（无状态单次）vs `StreamingHandler`（有状态多次 yield），`StepResult{output, events}` 统一 | Step 协议靠 `async_mode` 参数双态；流式单独一个 261 行 mixin；事件靠 `_fire` 回调副作用 | **事件跟着返回值走**才可测 |
| 4 | **`Pipeline` 组合**：codegen 的 LLM→提取→校验→执行是配置出来的 | 步骤顺序硬编码在 `_run_loop_core` | 组合性 |

---

## §1 两边现状实测

### 1.1 llmwikify 侧（设计本体）

| 文件 | 行数 | 内容 |
|---|---|---|
| `kernel/agent/loop.py` | **301** | UnifiedAgentLoop：PRECHECK → [REASON → DECIDE① → ACT → DECIDE② → OBSERVE → DECIDE③] → FINALIZE |
| `kernel/agent/_core_types.py` | **131** | StepResult / StepHandler / StreamingHandler / Pipeline |
| `kernel/agent/steps/` | **494** | checks / code / feedback / llm / transforms 五组实现 |
| `kernel/agent/spec.py` | 134 | BaseSpec / ChatSpec / CodegenSpec / UnifiedResult |
| `kernel/agent/context.py` | 58 | UnifiedContext |
| `kernel/agent/hook.py` | 80 | UnifiedHook 16 切面 |
| **合计** | **~1,198** | |

状态机循环（loop.py:67-234）一眼可读：每次迭代 = precheck → reason →
decide(after_reason) → act → decide(after_act) → observe → decide(after_observe)，
cancel/pause/confirmation 是显式 break 点，异常兜底 + finalize + done 事件收尾。

### 1.2 AgentNodes 侧（现状）

| 文件 | 行数 | 内容 |
|---|---|---|
| `loop.py` | **1,283** | AgentLoop 主体 + `_run_loop_core` 骨架 |
| `loop_tools.py` | 389 | 工具执行 mixin |
| `loop_compaction.py` | 284 | 压缩 mixin |
| `loop_progress.py` | 280 | 进度/心跳 mixin |
| `loop_stream.py` | 261 | 流式 mixin（`_stream_chat` / `_astream_chat`）|
| `loop_lifecycle.py` | 123 | 超时/上限/停止/指标 mixin |
| `loop_telemetry.py` | 93 | 追踪 mixin |
| `strategy/protocol.py` | — | 9 个 Step 协议（PreRun/LLMCall/Compaction/**Stop**/Continuation/Progress/Resilience/ToolExecution/Finalization）|
| `strategy/steps/` | — | 9 个对应实现 |
| `strategy/loop_strategy.py` | — | `LoopStrategy` 数据类（槽位制）|
| **loop 家族合计** | **~3,064** | |

**已有同构点**：`_run_loop_core` 已按
`self._strategy.pre_run → llm_call → tool_execution → finalization` 调用
（loop.py:797-1010），`_call_step` 统一 sync/async（`async_mode` 参数），
`factory.py` + `profile_resolver.py` 已实现模式注册（ReAct / explorer /
minimal / validator）。

### 1.3 拷贝过来的 unified/ 目录的问题

| 问题 | 实测 |
|---|---|
| **是 shim 不是本体** | `loop.py` 13 行 / `core.py` 34 行，全是 re-export |
| **import 全断** | 54 处 `from llmwikify.*`——AgentNodes 无此包，import 即崩 |
| **门禁误伤** | 层门禁报悬空 import；`check_kernel_nouns` 会扫到它的 `factor` 引用（`generate_factor_code`）|
| **缺本体** | 设计精华（301 行 loop + `_core_types` + steps/ 494 行）不在这份拷贝里 |

---

## §2 逐项评估：可取之处 6 条的真伪

用户列的 6 条可取之处，对照 AgentNodes 实测：

| # | 设计点 | 评估 | 落法 |
|---|---|---|---|
| 1 | 骨架 301 行单一真相 | ✅ **真差距**。AgentNodes 骨架与策略耦合（预算检查/假设创建/git 提交长在 `_run_loop_core` 里）| 抽出独立 `ReActSkeleton` |
| 2 | decider 显式可插拔 | ✅ **真差距**。StopStep 是固定槽位，不是按检查点位置配置 | `deciders: dict{"after_llm"→…, "after_tools"→…, "after_observe"→…}` |
| 3 | StepResult 统一事件+输出 | ✅ **真差距**。现在事件走 `_fire` 回调副作用，测试要抓回调 | 双接口改造 |
| 4 | Pipeline 组合 | ⚠️ 部分。`custom_steps.py` 有组合雏形，但主流程顺序硬编码 | 主流程保持骨架顺序，pipeline 用于 REASON/ACT 内部 |
| 5 | 双 handler 分类 | ✅ **真差距**。`async_mode` 双态 + 独立 stream mixin 是历史包袱 | 见 §3 风险 |
| 6 | hook 解耦 | ⚠️ 已接近。AgentNodes 有 `foundation/callback` + 进度/遥测 mixin；但 16 切面没有收敛成单一接口 | 收敛 `UnifiedHook` 等价物 |

---

## §3 接入方案：三选一

### 方案 A · 整体替换（不推荐）

把 AgentLoop 换成 UnifiedAgentLoop（移植 + 改包名），chat/exec/coding 全走新循环。

- ✅ 架构最干净，两个项目最终同构
- ❌ 压缩/权限/claim 校验/goal 注入/心跳/预算**全部要重新接成 handler**
  ——这些现在是 mixin，深度依赖 `_run_loop_core` 的内部时序
- ❌ 与第一阶段目标（14 条 checklist 可用）冲突：改引擎期间全部能力冻结
- ❌ 风险集中：一次替换，回归面 = 全部

### 方案 B · 增量收敛（推荐）

**以 UnifiedAgentLoop 为目标形态，分三步改造现有 loop，每步全量回归。**

| 步 | 内容 | 验收 |
|---|---|---|
| **B1** | 引入 `_core_types`（StepResult / StepHandler / StreamingHandler / Pipeline）到 `kernel/agent/`，与现有 `strategy/protocol.py` **合并成单一协议**：现有 9 个 Step 协议迁到新接口上（`async_mode` 参数消失，sync step 用 StepHandler 包裹） | 全量测试绿 |
| **B2** | 把 `_run_loop_core` 的停止判断抽成 `deciders` 字典（`after_llm` / `after_tools` / `after_observe`），现有 StopStep / ContinuationStep 变成 decider 的默认实现。**行为零变化** | 全量测试绿 + 14 条 checklist 全过 |
| **B3** | 骨架隔离：`_run_loop_core` 的循环体（precheck→reason→decide→act→decide→observe→decide→finalize）抽成独立 ~300 行类，预算/假设创建/git/goal 注入改为 hook（挂在 16 切面上）。mixins 变薄或消失 | 全量绿 + checklist 全过 + loop.py ≤ 400 行 |

- ✅ 每步可回滚，checklist 是安全网
- ✅ 最终形态 = llmwikify 设计（模板方法 + 策略 + decider 配置 + mode 注册表）
- ❌ 周期长于 A 的"一次性"，但每步都在可用状态

### 方案 C · 仅作参考（最保守）

不动现有 loop，把 llmwikify 代码当设计参考文档，逐条按需采纳。

- ✅ 零风险
- ❌ 两个项目继续分叉；「统一 agent 引擎」的目标落空

**推荐 B**。理由：AgentNodes 的 strategy 层证明团队（你）已经认可这个方向；
差距是结构性的（骨架隔离 / decider 配置 / 事件随结果）而非概念性的；
且第一阶段 14 条 checklist 恰好是 B 方案每一步的回归安全网——
**没有这份 checklist，B 方案不敢做；有了它，B 方案每步都可控。**

---

## §4 拷贝代码的处置

`src/agentnodes/kernel/agent/unified/`（19 文件，llmwikify 包名，import 全断）：

| 选项 | 说明 |
|---|---|
| ❌ 留在 src/ | 层门禁悬空 54 处、nouns 门禁误报、import 即崩——三输 |
| ❌ 直接删 | 设计参考丢了 |
| ✅ **移到 `reference/llmwikify/`**（仓库根，src 之外） | 保留参考、门禁不扫（`check_kernel_nouns` 只扫 `src/agentnodes/kernel`）、打包不含 |

**同时补齐本体**：把 llmwikify 的 `kernel/agent/{loop,_core_types,spec,context,hook}.py`
+ `steps/` 拷入 `reference/llmwikify/kernel-agent/`——这才是 301 行骨架与
两个抽象的真身（合计 ~1,198 行，含测试参照价值）。

B 方案 B1 步启动时，`reference/llmwikify/kernel-agent/_core_types.py`
就是移植源（改包名 `llmwikify` → `agentnodes`，其余不动）。

---

## §5 与第一阶段的关系

**顺序不变：先可用，后重构。**

```
第一阶段（当前）          B1/B2/B3（引擎收敛）
14 条 checklist ────────→ 每步的回归安全网
write 白名单 / shell 默认 ─→ 与 B1 无冲突（并行）
P1/P2 权限缺陷 ──────────→ 与 B2 decider 交界（decider 是 ask 的判定点之一）
```

具体交错：

- **现在 → 第一阶段完成**：只做 checklist 相关（write 白名单、shell 默认、
  P1 hard_deny、P2 路径规范化）。**引擎不动。**
- **第一阶段完成后 → B1**：协议统一（纯重构，行为零变化）
- **B2**：decider 配置化（行为零变化，但为 RSI 的「犯错才停」留口——
  **goal.settled 触发的停止、分歧行为，都是加一个 decider 的事**）
- **B3**：骨架隔离

**B2 与 RSI 的关联**（这是借鉴的战略价值）：llmwikify 的
`deciders` 让「什么情况停」变成配置。AgentNodes 第二步（RSI）的
裁决回滚、假基因清理触发、分歧行为——在旧结构里要改 loop 内部逻辑，
在新结构里是**注册新的 decider**。这就是把 Graph Engineering 的
「裁决信道」做成一等公民。

---

## §6 待决

1. **接入方案**：A / B / C（推荐 B）
2. **拷贝代码处置**：移 `reference/llmwikify/` 并补齐本体（推荐），还是你另有安排
3. **B1 启动时点**：第一阶段完成后（推荐），还是与 write/shell 修复并行
