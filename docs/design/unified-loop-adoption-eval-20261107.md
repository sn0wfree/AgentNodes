# UnifiedAgentLoop 借鉴实施评估（v1.0 · 2026-11-07）

> **上位文档**：`unified-loop-adoption-20261107.md`（三方案对比，推荐 B）。
> 本文是用户要求的**深度评估**：逐概念对照两边实现、每项给裁决
> （采纳/改造/保留/拒绝）、B1/B2/B3 落到 file:line、
> 以及**采纳后怎么用**（使用故事）。
>
> **用户立场**：第一阶段尚未收尾——引擎形态是「通用 agent」定义的一部分，
> E1–E5 只验了能力，没验载体。本文采纳该立场并给出收敛时序。

---

## §0 一句话结论

**借鉴方式 = 换底座，不换职责。**
AgentNodes 的 9 个 Step 协议在**职责划分**上比 llmwikify 更细
（预算/韧性/进度是 llmwikify 没有的），llmwikify 的设计在**协议形态**上
更对（双接口 + 事件随结果 + decider 配置 + mode 注册表）。
B1/B2/B3 = 用 llmwikify 的形态重铸 AgentNodes 的职责，一个都不丢。

---

## §1 逐概念对照（含行数证据）

| 概念 | llmwikify | AgentNodes 现状 | 裁决 |
|---|---|---|---|
| **循环骨架** | `UnifiedAgentLoop.run_stream` 301 行独立类；PRECHECK→[REASON→DEC①→ACT→DEC②→OBSERVE→DEC③]→FINALIZE | `_run_loop_core`（loop.py:776-1020）埋在 1283 行类里，与预算/trace/goal/假设创建/git 混居 | **B3 采纳**：抽独立骨架 |
| **步骤接口** | `StepHandler.handle(input,spec,ctx)`（无状态单次）/ `StreamingHandler.stream(...)`（有状态多次 yield，末位 yield 即结果）——**两个接口，不靠参数切换**（_core_types.py:51-96） | `Step.execute(ctx, *, async_mode)`（protocol.py:40）——**一个接口 + bool 参数双态**，调用方 `_call_step` 手工分支 | **B1 采纳**：双接口替换双态参数 |
| **步骤返回** | `StepResult{output, events, success, error}`——**SSE 事件跟着返回值走**（_core_types.py:23-45） | 步骤**变异 LoopContext** + 事件经 `_fire`/emit **回调副作用**（loop.py:802） | **B1 采纳**：StepResult 进协议 |
| **组合** | `Pipeline(*steps)` 串行 fail-fast，events 累积（_core_types.py:102-131） | `custom_steps.py` 有雏形；主流程顺序硬编码在骨架里 | **B1 采纳**（Pipeline）；主流程顺序保留在骨架（那是模板方法的"模板"） |
| **停止判定** | `deciders: dict{after_reason/after_act/after_observe}`——**三个检查点显式可插拔**（loop.py:124/201/217） | `StopStep.evaluate(ctx)→(stop,reason)` + `ContinuationStep` 固定槽位 + `ctx.should_stop` **副作用约定**（protocol.py:84-98） | **B2 采纳**：deciders dict；现有 Stop/Continuation/Progress/Resilience **降位为默认 decider** |
| **模式注册** | `AgentModeConfig{name, reasoner, actor, deciders, spec_cls, precheck, finalize, hook_factory}` + `create_agent_loop("chat")`（registry.py:41-99） | `LoopStrategy` dataclass 槽位制（loop_strategy.py:55）+ `factory.py`/`profile_resolver.py`（ReAct/explorer/minimal/validator） | **B2 融合**：LoopStrategy 槽位 → AgentModeConfig 形态；factory 保留为注册表前端 |
| **hook** | `UnifiedHook` 16 切面 + CompositeHook（hook.py，80 行） | 5 个 mixin（compaction 284/progress 280/stream 261/tools 389/telemetry 93）+ `_fire(name, ctx, *args)`（loop.py:802） | **B3 改造**：mixin 解体为 hook 实现；切面数以 AgentNodes 现有 `_fire` 调用点为准（≈16，惊人地对齐） |
| **上下文** | `UnifiedContext{spec, messages, steps, tools_used, usage, stop_reason...}`（58 行） | `LoopContext`（strategy/loop_context.py） | **保留 LoopContext**，字段对齐 UnifiedContext（加 stop_reason/elapsed_sec） |
| **Spec** | `BaseSpec/ChatSpec/CodegenSpec`（spec_cls 可换，registry.py:54） | `AgentLoop.__init__` **42+ 构造参数**（loop.py:256-430） | **B3 采纳**：参数收进 `LoopSpec` dataclass（构造参数爆炸是骨架不隔离的根因之一） |
| **codegen 流水线** | `Pipeline(LLMCall, ExtractCode, ValidateSyntax, ValidateSafety, ExecuteCode)`（codegen_pipeline.py 257 行） | 无对应 | **拒绝进 kernel**：`generate_factor_code` 是业务；**机制**（Pipeline）B1 已采纳，第三步工具包 agent 自己组合 |

## §2 怎么用：四个使用故事（采纳后的形态）

### 2.1 chat（替换 `build_chat_agent_loop` 内部）

```python
# kernel/agent/modes.py（B2 后）
register_mode(AgentModeConfig(
    name="chat",
    spec_cls=ChatLoopSpec,                     # 现 42+ 构造参数收拢
    reasoner=StreamingLLMReasoner(),           # 现 loop_stream.py 的流式调用
    actor=ToolActor(),                         # 现 loop_tools.py 的工具执行
    deciders={                                 # 现 loop_lifecycle 的散落 if
        "after_reason": StopOnNoToolCalls(),   # ← CheckEmptyStep 同型
        "after_act": CircuitBreakerDecider(),  # ← ResilienceStep
        "after_observe": CompositeDecider(     # ← StopStep+Continuation+Progress
            MaxIterationsDecider(), RepeatHashDecider(), GoalContinuationDecider(),
        ),
    },
    hook=CompositeHook(BudgetHook(), TelemetryHook(), ProgressHook()),
))
# 调用方：
loop = create_agent_loop("chat", config=..., workspace=...)
```

### 2.2 RSI 裁决（第二步的战略收益，重申）

```python
# 分歧行为 = 换一个 decider，不改 loop 一行
deciders["after_act"] = GenomeGateDecider(gate, trip_halt)
```

Graph Engineering 的「③ 裁决信道」成为一等公民——这是采纳的**战略理由**。

### 2.3 第三步工具包 agent

量化/法律/医学 agent = 换 `reasoner/actor/deciders` 组合 + 各自 spec_cls，
注册成新 mode。平台提供骨架与协议，插件提供策略。

### 2.4 两项目同引擎

B1–B3 落地后，llmwikify 的 `kernel/agent/*` 改为
`from agentnodes.kernel.agent import ...`（它已是薄 shim 层）——
**llmwikify 的 chat 生产路径跑在 AgentNodes kernel 上**。
这是「AI 操作系统」叙事的第一性证明。

---

## §3 B1/B2/B3 实施细化（file:line 级）

### B1 · 协议统一（1 批，行为零变化）

| 动作 | 文件 | 细节 |
|---|---|---|
| 新增 | `kernel/agent/_core_types.py` | 从 `reference/llmwikify/kernel-agent/_core_types.py` 移植：StepResult/StepHandler/StreamingHandler/Pipeline；**`maybe_await` 用 AgentNodes 现有 sync/async 桥** |
| 适配 | `strategy/protocol.py` | 9 个 Protocol 保留职责；`execute(ctx, *, async_mode)` 标记 **DEPRECATED**，新接口 `handle(input, ctx) -> StepResult`；现有 steps/ 加适配器（10 个文件各 +10 行） |
| 流式 | `loop_stream.py` | `_stream_chat/_astream_chat` 包成 `StreamingLLMReasoner(StreamingHandler)`；**mixin 先不删**（B3 再拆），新路径与旧路径并行灰度 |
| 验收 | 全量 + batch_gate + capability_smoke | 行为零变化：chat/E2E 全过 |

### B2 · decider 配置化（1 批，行为零变化）

| 动作 | 文件 | 细节 |
|---|---|---|
| 新增 | `kernel/agent/deciders.py` | `Decider`（= StepHandler 子协议，`handle(...) -> StepResult{output=(stop,reason)}`）+ 默认实现：`MaxIterationsDecider` / `StopOnNoToolCalls`（←StopStep）/ `GoalContinuationDecider`（←ContinuationStep）/ `RepeatHashDecider`（←ProgressStep）/ `CircuitBreakerDecider`（←ResilienceStep）|
| 改造 | `loop.py::_run_loop_core` | 三处检查点改为查 `self._deciders.get("after_reason"/"after_act"/"after_observe")`；**默认 dict 由 LoopStrategy 组装**（现槽位映射进检查点：Stop/Continuation→after_reason，Resilience/Progress→after_act/after_observe） |
| 新增 | `kernel/agent/modes.py` | `AgentModeConfig` + `register_mode/create_agent_loop`（从 registry.py 移植，`spec_cls` 先不启用——B3） |
| 验收 | 全量 + 一个「新 decider 注入即生效」的测试 | 零行为变化证明：默认组合下所有既有断言不动 |

### B3 · 骨架隔离（1–2 批，行为零变化）

| 动作 | 文件 | 细节 |
|---|---|---|
| 新增 | `kernel/agent/skeleton.py`（目标 ≤350 行） | `ReActSkeleton.run_stream(spec)`：循环体纯化——迭代/precheck(cancel/pause/budget)/REASON/DEC/ACT/DEC/OBSERVE/DEC/FINALIZE/done 事件 |
| 迁出 | loop.py 各段 → hooks | 预算（`_check_budget`）→ BudgetHook；假设自动创建（:1209）→ PreRunHook；goal 注入（:1253）→ PreRunHook；git 提交（:1269）→ FinalizeHook；trace（:1103）→ TelemetryHook |
| 收拢 | `LoopSpec` dataclass | 42+ 构造参数收拢；AgentLoop 变薄壳（构造 spec + 持 skeleton） |
| 解体 | 5 个 mixin | compaction/stream/tools/progress/telemetry → 对应 handler/hook；`loop.py` 目标 ≤400 行 |
| 验收 | 全量 + capability_smoke + **E1–E5** | checklist 是安全网 |

---

## §4 时序：与第一阶段的关系（回应「未收尾」）

用户的判断**成立**，修正后的定位：

```
第一阶段（当前批次）           引擎收敛
──────────────────────       ─────────────
1-C/D/E 资产迁 L2  ──┐
1-F 门禁扩展       ──┤── 并行
E6 删 apps 验收    ──┘        │
                              ▼
E1–E5 能力验收 ✅        B1 协议统一（第一阶段架构收尾）
C6–C14 剩余 checklist ──→ B2 decider 配置化（同上）
                         B3 骨架隔离（第二阶段开头，RSI 前）
```

**理由**：C6–C14（压缩/中断/恢复/流式细节）全部**长在引擎上**——
在 1283 行 mixin 骨架上做 C9/C13 是给坏地基加层。
所以 **B1+B2 升格为第一阶段架构验收项**：引擎收敛完成前，
第一阶段不算完成。B3 可推迟到第二阶段开头（RSI 之前），
因为 B1+B2 后 deciders 已可用（RSI 裁决不依赖 B3）。

---

## §5 风险

| # | 风险 | 缓解 |
|---|---|---|
| 1 | 双接口迁移期 steps 行为漂移 | 适配器逐个上；每步全量 + E1–E5 |
| 2 | deciders 三检查点映射错位（现 if 散在 4 处） | B2 先写**映射表测试**：枚举现有停止路径（超时/上限/空响应/goal/熔断/重复 hash）→ 断言新 decider 组合同判定 |
| 3 | B3 动 mixin 引回归 | mixin 保留为薄兼容层一个批次，E1–E5 全过后删 |
| 4 | llmwikify 反接时机 | 不在 B1–B3 内做；单独一批（它有生产流量） |
