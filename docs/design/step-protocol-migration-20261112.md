# Step 协议迁移 B2-①：`execute(ctx, async_mode) → handle(input, ctx) → StepResult`

> **上位设计**：`loop-final-design-20261112.md` §0 公理四、§1.2 变形族、§9 差异速查。
> **性质**：**运行时行为零变化**的形状迁移（一处显式例外见 §3.6：custom
> `pre_run` 从「静默忽略」变为「生效」——修 trap，仓内零覆盖）。
> 改的是签名与错误传递方式，不改任何判定时点、事件顺序、停止原因文本。
> **本批不碰事件**：`_emit` / `_afire_hooks` 仍是步骤内的副作用，
> 搬进 `StepResult.events` 属 B2-②。
>
> **路径约定**：正文里的裸文件名按以下目录解析——
> `loop.py` / `loop_tools.py` / `loop_engine.py` / `loop_engine_adapters.py` /
> `deciders.py` / `modes.py` / `step_adapters.py` / `_core_types.py` →
> `src/agentnodes/kernel/agent/`；
> `pre_run.py`/`compaction.py`/`llm_call.py`/`tool_execution.py`/`finalization.py` →
> 其下 `strategy/steps/`；
> `protocol.py` / `loop_context.py` / `loop_strategy.py` / `custom_steps.py` /
> `validator.py` / `minimal.py` / `factory.py` → 其下 `strategy/`。
> 行号均为 2026-11-12 `7e68ac77` 时点实测。

---

## §0 结论摘要

| # | 事 | 数字（实测） |
|---|---|---|
| 1 | 要改签名的 step 类 | **7**（5 个执行 step + 2 个替换 step）|
| 2 | 生产调用点 | **8**（legacy `loop.py` 4 + 引擎适配器 4）|
| 3 | `async_mode` 在 `src/` 的出现 | **50 → 18**（消 32 处 = 64%）。删除面逐文件实测：`strategy/` 17 + `step_adapters.py` 4 + `loop_engine_adapters.py` 4 + `loop.py` 6 + `_core_types.py` 文档 1。残留 18 = `loop.py` 8（`_run_loop_core` / `_get_response`）+ `loop_tools.py` 10，全在 U6-2 删除面 |
| 4 | `async_mode` 在 `tests/` 的 85 处中，本批涉及 | 6 文件 **42** 处 = `step.execute(...async_mode=)` **26** + 测试内替身签名/桩 **7** + 属性名与调用处 **9**；其余 43 处在 `test_capability_smoke` / `test_dual_path_permission` 等（`_run_loop_core` / `_execute_tool_call_core`，U6-2）|
| 5 | `protocol.py` 的 Protocol | **10 → 4**（6 个执行型并入 `StepHandler`，`Step` 因 `should_run` 零调用一并删）|
| 6 | `should_run` 生产调用点 | **0**（3 处定义 + 1 处 Protocol 声明，全是死扩展点）|
| 7 | `LegacyStepAdapter` 生产消费者 | **0**（它包的是即将不存在的签名）|
| 8 | `pre_run` 接线（本轮裁决：**接**）| `LoopPrepare` 从直调 `_prepare_run`（adapters:164）改为委托 `strategy.pre_run` 槽位；双跑风险变成 grep 网（§3.6）|

**一句话**：把「步骤通过 `ctx.should_stop` 副作用表达失败」换成「步骤返回
`StepResult`」；把「`async_mode` 布尔两套分派」换成「单一 async 形状」；
把「10 个手写 Protocol」换成「1 个现成 `StepHandler` + 4 个判定型」；
把 `pre_run` 死槽位接线成真的。

---

## §1 现状实测

### 1.1 7 个 step 类，两种形状，一个 bool

| 文件 | 类 | 行 | 形状 |
|---|---|---|---|
| `strategy/steps/pre_run.py` | `DefaultPreRunStep` | 52 | `execute(ctx, *, async_mode) -> ctx`（同步）|
| `strategy/steps/compaction.py` | `DefaultCompactionStep` | 56 | `async execute(...) -> ctx` |
| `strategy/steps/llm_call.py` | `DefaultLLMCallStep` | 92 | `async execute(...) -> ctx` |
| `strategy/steps/tool_execution.py` | `DefaultToolExecutionStep` | 77 | `async execute(...) -> ctx` |
| `strategy/steps/finalization.py` | `DefaultFinalizationStep` | 52 | `async execute(...) -> ctx` |
| `strategy/custom_steps.py` | `ClaimValidationFinalizationStep` | 63（两类共）| `execute(ctx, *, async_mode) -> ctx`（同步）|
| `strategy/custom_steps.py` | `NoOpToolExecutionStep` | 同上 | `execute(ctx, *, async_mode) -> ctx`（同步）|

四个判定型槽位（`stop`/`continuation`/`progress`/`resilience`）**形状本来就不同**
（`evaluate(ctx) -> (bool, reason)` / `record_hash` / `is_no_progress` / `is_open`），
**本批不动它们**——§5 说明原因。

### 1.2 「返回值」是假的：`ctx` 变异 + 3 个 metadata 键

5 个执行 step 的每个 `return` 语句返回的都是**同一个 ctx**（实测：pre_run L40/L49、
compaction L39/L53、llm_call L43/65/69/89、tool_execution L39/43/74、finalization L37/L49）。
真正的"产出"藏在这些地方：

| step | 真正产出 | 传递方式 | 消费者 |
|---|---|---|---|
| `pre_run` | `full_task`, `result`, `messages`, `t0` | 写 ctx 字段 + `metadata["full_task"]` | **0（§1.4 死代码位——本批接线后消费者 = `LoopPrepare`，见 §3.6）** |
| `compaction` | `messages'`, `applied` | 写 `ctx.messages` + `metadata["compaction_applied"]` | 前者两路径都读；后者**只有测试读** |
| `llm_call` | `response`, 失败/空响应信号 | 写 `ctx.response` + **`ctx.should_stop` / `ctx.stop_reason`** | 两路径都读 |
| `tool_execution` | `tool_hashes`, `tool_result_msgs` | `metadata["tool_hashes"]` / `metadata["tool_result_msgs"]` | 前者两路径读；后者**只写不读**（见 §1.7）|
| `finalization` | 无（纯副作用：metrics / claim / after_run）| — | — |

**`ctx.should_stop` 是本批的核心病灶**：它让「步骤失败」变成调用方必须记得去
poll 的隐藏状态。8 个调用点里 4 个在 poll 它（`loop.py:1203/1282`、
`loop_engine_adapters.py:94/148`），另外 4 个（compaction ×2、finalization ×2）
**不 poll——所以它们失败了会静默继续**。这不是设计，是漏检。

### 1.3 `async_mode`：同步那半边已无生产调用者

调用链实测：

```
run()  ──→ _run_coro_in_sync ──→ arun()
arun() ──→ _run_via_skeleton()                       ← 默认引擎
       └─→ _run_loop_core(async_mode=True)           ← loop.py:918，唯一的 src 调用点
```

`async_mode=False` 的**唯一**入口是 `_run_loop_core(..., async_mode=False)`，
而它只被 `tests/test_dual_path_permission.py:113` 调用。也就是说：

- `_get_response(async_mode=False)` 分支（`loop.py:1374/1384/1401`）
- `compaction` 的 `loop._maybe_compact` 分支（`compaction.py:45`）
- `tool_execution` 的 `loop._execute_tool_batch` 分支（`tool_execution.py:52`）
- `_execute_tool_call_core(async_mode=False)` 及其 `tool.invoke` / `time.sleep` 分支

**在 U5.5 统一入口之后，全部生产不可达。** 它们还在，只是因为 `_run_loop_core`
的形参还在（U6-2 才删）。

**结论**：从 step 签名里删 `async_mode` **不需要**保留同步分支——没有生产调用者
会走到 `False`。`_get_response` / `_maybe_compact` / `_execute_tool_batch` 的同步
孪生也保持原样（U6-2 范围），step 里改传字面量 `True`。

### 1.4 `pre_run` 是明确的死代码位

`pre_run.py:7-11` 自己写着：

> ⚠ 互斥约定：本 step 与 `_run_loop_core` 内联的 `_prepare_run` 调用**互斥**——
> 主循环当前从不调用 `self._strategy.pre_run`，本 step 是死代码位。

实测确认：`src/` 里 `_strategy.pre_run` 的调用点 **0 处**（只有 docstring 提它）。
引擎路径用 `LoopPrepare`（`loop_engine_adapters.py:156`）直接调 `_prepare_run`。

**本批接线它**（2026-11-12 裁决）：`LoopPrepare` 改为委托
`strategy.pre_run` 槽位，直调 `_prepare_run` 的位置删掉。详案 §3.6——
包括失败语义为何必须保持"崩溃"而不是 fail-result，以及双跑风险如何
变成 grep 网。

### 1.5 `should_run` 是「有 API 无消费」

| 位置 | 内容 |
|---|---|
| `protocol.py:45` | `Step.should_run(ctx) -> bool` 声明 |
| `pre_run.py:35` / `compaction.py:32` / `llm_call.py:37` | 3 处实现，**全部 `return True`** |
| `tool_execution.py` / `finalization.py` | **没有实现**（协议成员没实现也能过 isinstance——`runtime_checkable` 只看名字在不在）|
| 生产调用点 | **0** |

`test_compaction_step_migration.py:110` 断言它返回 True——测的是一个**永远不会被
调用的方法**。

### 1.6 顺带发现：`tool_result_msgs` 是只写不读的死变量

`loop.py:1285`：

```python
tool_result_msgs = tool_ctx.metadata.get("tool_result_msgs") or []
```

AST 实测（`_run_loop_core` 函数体内该名字的全部 Name 节点）：
`1267 Store`（circuit-breaker 分支自己的局部值）、`1268 Load`（同一分支读）、
`1285 Store`——**1285 之后再无任何 Load**。

即：`tool_execution` step 写进 `metadata["tool_result_msgs"]` 的那个列表，
legacy 取出来赋给局部变量后**丢掉**。`test_tool_execution_step_hooks.py:173`
断言 `len(out.metadata["tool_result_msgs"]) == 1` 是真的（键确实被写），
但**没有任何生产代码消费它**。

**本批处置：只记录，不删。** 它是无关死代码（AGENTS.md 原则 3：发现无关死代码 → 提，不删），
且 B2-① 的 `tool_execution` 迁移会把它一并放进 `output`——那时它就从"死变量"
变成"未被消费的 output 键"，语义更明确。是否删除归 U6-2 一并判断。

### 1.7 `LegacyStepAdapter`：桥的对面要拆了

`step_adapters.py:55` 的核心一行是 `self._step.execute(ctx, async_mode=True)`。
B2-① 之后 `execute(ctx, *, async_mode)` 不存在，这个适配器**没有可适配的对象**。
消费点实测：只有 `tests/test_core_types.py`（163/184/198/216）与两处 docstring，
**零生产消费者**。

`FnStreamingHandler`（同文件）不同——它是 B2-② 接 `_astream_chat` 的现成壳，**保留**。

---

## §2 目标契约

### 2.1 一句话公理（本批新增，可被评审逐条检验）

> **`ctx` 是累积态，`StepResult.output` 是本步产物。**
>
> - 对累积态的**追加**留在 ctx 上（`messages.append`、`result.messages`）——
>   因为下游全部持有同一个 list 对象，替换对象会静默丢消息（V2 已踩过，
>   `loop_engine_adapters.py:86-92` 有实测记录）。
> - 对累积态的**替换**与本步算出的**标量/列表**进 `output`——因为它们没有
>   "同一对象"约束，塞进 ctx 只是用一个共享可变字典传参。
> - **失败/停止**不再写 `ctx.should_stop`，改由 `StepResult.success` / `error` 表达。

这条公理让每条"这个字段该放哪"的判断有唯一答案，也解释了为什么 `messages`
两边都有（llm_call 追加 → ctx；compaction 替换 → output）。

### 2.2 统一形状

```python
class StepHandler(ABC):                       # kernel/agent/_core_types.py:69（现成）
    def handle(self, input, ctx) -> StepResult | Awaitable[StepResult]: ...
```

```python
@dataclass
class StepResult:                             # _core_types.py:38（现成）
    output: Any = None
    events: list[dict] = field(default_factory=list)   # ①恒空；②才用
    success: bool = True
    error: str | None = None
```

step 侧**统一写 `async def handle`**（全部 I/O 都是 async，没有理由让
`maybe_await` 在热路径上每次 `inspect.isawaitable`）。`maybe_await` 保留给
**调用方**用——因为自定义 step 可能仍返回裸 `StepResult`，契约必须容忍。

### 2.3 五个执行 step 的 input / output 表

| step | `input` | `output` | 仍写 ctx 的字段 | 失败表达 |
|---|---|---|---|---|
| `pre_run` | `ctx.task/context/history` | `{"full_task", "result", "messages", "t0"}` | `task`（原始→full_task）/`messages`/`result`/`iteration`/`t0`/`metadata["full_task"]` | **异常向上传播（不经 `_call_step`）**——见 §3.6 失败契约 |
| `compaction` | `ctx.messages` | `{"messages", "applied"}` | `metadata["compaction_applied"]`（保持，测试在读）| 异常 → `fail` |
| `llm_call` | `ctx.messages`, `ctx.iteration` | **`LLMResponse` 本身**（`None` = 空响应）| `response`/`response_content`/`response_was_tool_call`/`ctx_hash_id` + 追加 assistant 消息 | LLM 异常 → `fail("error")` 且**仍调** `_handle_llm_error`（它写 `result.error`/`finished_reason`，是既有可观测契约）|
| `tool_execution` | `LLMResponse` | `{"tool_hashes", "tool_result_msgs"}` | 追加 tool 消息 | 异常 → `fail` |
| `finalization` | `ctx.result/messages/t0` | `None` | —（`after_run` 副作用保持）| 异常 → `fail` |

`llm_call` 的 `None` vs `fail` 的区别是**必须保留的**：
`None` → `stop_reason="llm_none"`（服务端返回空，非异常）；
`fail` → `stop_reason="error"` + `result.error` 已由 `_handle_llm_error` 填好。

### 2.4 `_call_step` 的新契约

```python
async def _call_step(self, step: Any, ctx: Any) -> StepResult:
    """Execute a StepHandler with uniform error isolation."""
    try:
        return await maybe_await(step.handle(None, ctx))
    except Exception as exc:  # noqa: BLE001 — isolated per-step
        logger.warning("step %s failed: %s", step.name, exc)
        return StepResult.fail(f"{step.name}: {exc}")
```

三处变化：

1. `async_mode` 形参删除
2. `step.execute(ctx, async_mode=...)` → `step.handle(None, ctx)`
3. **异常不再改 `ctx`**——`ctx.should_stop` 那两行副作用移出。
   `stop_reason = f"step_{name}"` 的**取值**由调用方原样写回（保持既有可观测文本，
   `test_loop_v2_decision_migration.py:161` 钉的就是这个字符串）。

**名字保留**（`_call_step`）：全仓 8 个调用点 + 2 个测试引用它，改名是纯噪音，
且 `loop-final-design` §9 没列这一项。改名归 U6-2 的"命名收尾"。

---

## §2A 逻辑流（新契约下的一轮迭代）

统一引擎 `run_stream` 的状态机**零改动**——闸位、decider、FINALIZE 漏斗全部
不动。变化只在 5 个 step 的调用形状与读回方式。▲ = 本批变化点：

```
run_stream(spec)
  ▲ prepare: LoopPrepare.__call__(spec)
  │   ctx(task=spec.task) → strategy.pre_run.handle(None, ctx)   ← 直调·sync·异常向上崩（§3.6）
  │   ctx.task ← output["full_task"]; hook_ctx; attach_pause; raw_task; _skeleton_ctx
  │   [loop_start 事件 ×1]
  │
  for iteration 1..N:
  │   on_iteration_start（wrap_up nudge，不动）
  │   ── PRECHECK 闸 ── MaxIter → Budget → CircuitBreaker → ExternalTermination
  │        STOP → break → FINALIZE
  │   ── REASON（LoopReasoner.handle，内部）──────────────────
  │   ▲ ① compaction.handle(None, ctx) → StepResult{output={messages', applied}}
  │        成功 → ctx.messages ← output["messages"]（回写，防 V2 丢消息）
  │        失败 → 只日志，继续（吞没语义显式化——今天也是吞没）
  │      ② inbox drain + order==0 injectors（不动）
  │   ▲ ③ llm_call.handle(None, ctx) → StepResult{output=LLMResponse | None}
  │        r.success=False → 回填 llm_ctx.should_stop（stop_reason ||= "step_llm_call"）
  │        业务写保留（"error" / "llm_none" → StopSignalDecider 通道）
  │        ctx.response ← r.output；消息回写；预算记账；order≥100 injectors（不动）
  │   ── DECIDE① after_reason ── Budget → GoalCont → InjectorCont → StopSignal → TextFinal
  │        STOP → break（stop_reason 文本不变）／ CONTINUE → 下一轮
  │   ── pre_act（空闸 PROCEED——P2 backlog，不动）
  │   ── ACT（LoopActor.handle，内部）────────────────────────
  │   ▲ ④ tool_execution.handle(response, ctx) → StepResult{output={hashes, msgs}}
  │        r.success=False → 回填 tool_ctx.should_stop（"step_tool_execution"）
  │        ctx.messages 回写；record hashes（不动）
  │   ── DECIDE② after_act ── NoProgress（approval_gate → EXIT / PROCEED）
  │        EXIT → _ending_emitted → break → FINALIZE
  │   ── OBSERVE ── after_iteration hook ── DECIDE③ after_observe（空）
  │
  for-else: max_iter 兜底（finished_reason="max_iter"，不动）
  ── FINALIZE 漏斗（幂等，恰一次）────────────────────────────
  ▲ ⑤ finalization.handle(None, ctx) → StepResult{output=None}
      失败只日志（不中断，现状）；git_commit → trace close → after_run(幂等) → done
```

legacy `_run_loop_core`：同样 5 个 step、同样的读回与回填规则，宿主是
瞬态 ctx + 局部变量；判定内联点（1203/1250/1264/1282/1303）逐字保留，
随 U6-2 删除。

## §2B 操作流（一次 step 调用的微序列）

```
调用方（LoopReasoner / LoopActor / legacy 循环体）
  │
  ├─ ① 构造瞬态 ctx（_make_strategy_ctx，现状保留）
  ├─ ② r = await loop._call_step(step, ctx)
  │      _call_step 内部（唯一分派点，改用 maybe_await——三处同型收口第一处）：
  │        step.handle(None, ctx)
  │          ├─ 返回 StepResult → 原样上抛
  │          └─ 抛异常 → logger.warning → StepResult.fail(f"{step.name}: {exc}")
  │               【唯一变化】不再写 ctx.should_stop（基线 loop.py:665-666 删）
  ├─ ③ 读回（按调用点二分）：
  │      读 stop 通道 4 站（llm ×2 / tool ×2）→ 回填（§4 模式）
  │      不读的 4 站（compaction ×2 / finalization ×2）→ 只取 output，失败仅日志
  ├─ ④ 数据产物：r.output["messages"] / r.output / None
  └─ ⑤ 状态回写：messages 4 处（V2 防丢）＋ metadata 双写（hashes / applied）
```

**失败翻译阶梯**（从 step 到出口；三个案例的事件流与今天逐事件一致）：

```
① LLM 业务错误（LLMError）
   llm_call 捕获 → _handle_llm_error（result.error / finished_reason="error" / error 事件）
                 → on_error hook → ctx.should_stop=True, stop_reason="error"
                 → StepResult.fail("error")
   调用点：r.success=False 但 stop_reason 已写 → 回填只补 should_stop（幂等）
   传播：adapters:95/96 → 引擎 ctx → DECIDE① StopSignal → STOP "error"

② 意外崩溃（非业务）
   handle 抛 → _call_step catch → fail("llm_call: …")        【不再写 ctx】
   调用点：r.success=False 且 stop_reason 空 → 回填 "step_llm_call"
   传播 → StopSignal → STOP "step_llm_call"                  【文本与今天一致】

③ 空响应
   _get_response → None → llm_call 写 should_stop/"llm_none" → ok(None)
   调用点：r.success=True → 无回填 → 传播 "llm_none" → STOP "llm_none"
```

## §2C 案例走查（7 条）

**案例 1 · 正常工具轮（happy path，引擎路径）**
```
PRECHECK 全 PROCEED
REASON:  compaction → ok({messages, applied=[]}) → ctx.messages 回写
         llm_call → ok(LLMResponse{tool_calls:[read_file]}) → ctx.response ← r.output
DECIDE①: 全 PROCEED（有 tool_calls，TextFinal 不触发）
ACT:     tool_execution → ok({tool_hashes:["a1"], …}) → 消息回写
DECIDE②: NoProgress.record(["a1"]) → is_no_progress=False → PROCEED
OBSERVE → 下一轮
```

**案例 2 · LLM 业务错误（LLMError）**
```
REASON:  llm_call 捕获 → _handle_llm_error → on_error hook
         → ctx.should_stop=True, stop_reason="error" → StepResult.fail("error")
调用点:  r.success=False；stop_reason 已写 → 回填只补 should_stop（幂等）
传播:    adapters:95/96 → 引擎 ctx → DECIDE① StopSignal → STOP "error"
结论:    事件序列 / stop_reason 逐字节一致——llm_call 的业务写保留是关键
```

**案例 3 · 空响应（llm_none）**
```
_get_response 流回退失败 → None（非异常）
llm_call → should_stop=True / "llm_none" → ok(None)
调用点:  r.success=True → 无回填；r.output is None → 传播 "llm_none"
DECIDE①: StopSignal → STOP "llm_none"
反证:    若删掉这条业务写，StopSignal 恒 PROCEED → 空响应烧到 max_iter
```

**案例 4 · llm_call 意外崩溃（非 LLMError）**
```
handle 抛 RuntimeError → _call_step catch → fail("llm_call: RuntimeError: …")
  【基线 loop.py:665 的 ctx 写已删】
调用点:  r.success=False 且 stop_reason 空 → 回填 "step_llm_call"
传播 → StopSignal → STOP "step_llm_call"
对比今天: _call_step 曾写 transient ctx（同文本）→ 文本逐字节一致
```

**案例 5 · compaction 崩溃（吞没语义显式化）**
```
compaction.handle 抛 → fail("compaction: …") →【该站不读 success，无回填】
LoopReasoner: 只日志，ctx.messages 不更新，继续 llm_call
对比今天: _call_step 写过 transient ctx.should_stop=True，但无人 poll → 同样继续
差异:    今天的吞没藏在 _call_step；B2-① 后在调用点显式「不读」——行为一致，意图可见
```

**案例 6 · tool_execution 崩溃（两路径现状镜像，非本批引入）**
```
引擎路径: fail("tool_execution: …") → 回填 "step_tool_execution" → 传播
          after_act PROCEED（NoProgress 不读 should_stop）→ 不提前 break
          → 下一轮 REASON 烧一次 LLM → DECIDE① StopSignal → STOP
legacy:   回填同 → loop.py:1282-1284 立即 fire after_iteration + break（不烧 LLM）
证据:     adapters:144-153 无早退 vs loop.py:1282-1284 即断
裁定:     现状镜像，两侧各自保形（本批零变化）；是否收敛归等价性 / U6 批
```

**案例 7 · pre_run 接线（custom spy）**
```
run_stream → LoopPrepare.__call__(spec)
  ctx = LoopContext(task=spec.task)
  r = strategy.pre_run.handle(None, ctx)     ← 直调（sync；不走 _call_step，异常向上崩）
  custom spy: count = 1
  ctx.task ← r.output["full_task"]; hook_ctx; attach_pause; raw_task; _skeleton_ctx
[loop_start ×1 —— spy 网断言恰 1 次]
主循环照常。legacy 不经过 step（内联 _prepare_run），互斥约定按路径成立
```

---

## §3 逐 step 迁移形态

### 3.1 `compaction`（代表性样例，其余四个同构）

```python
# 前（56 行）
async def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext:
    loop = self._loop
    if loop is None:
        return ctx
    if async_mode:
        messages, applied = await loop._amaybe_compact(ctx.messages)
    else:
        messages, applied = loop._maybe_compact(ctx.messages)     # ← 生产不可达
    if applied:
        loop._emit_compaction(applied, ctx.iteration, ctx.result)
    loop._emit_iter_start(ctx.iteration, messages)
    ctx.messages = messages
    ctx.metadata["compaction_applied"] = applied
    return ctx
```

```python
# 后
async def handle(self, input: Any, ctx: LoopContext) -> StepResult:
    loop = self._loop
    if loop is None:
        return StepResult.ok({"messages": list(ctx.messages), "applied": []})
    # 唯一生产路径（U5.5 后 async_mode=False 无调用者，§1.3）
    messages, applied = await loop._amaybe_compact(ctx.messages)
    if applied:
        loop._emit_compaction(applied, ctx.iteration, ctx.result)   # ①仍是副作用
    loop._emit_iter_start(ctx.iteration, messages)
    ctx.metadata["compaction_applied"] = applied
    return StepResult.ok({"messages": messages, "applied": applied})
```

**注意 `ctx.messages` 不再被写**——替换态走 output。调用方接：

```python
r = await self._call_step(step, comp_ctx)
if not r.success:
    ctx_stop(f"step_compaction"); break
messages = r.output["messages"]
```

### 3.2 `llm_call`（唯一有失败语义的 step）

关键差异只有三处：`async_mode` 传字面量 `True`、异常返回 `fail`、`None` 稳态返回 `ok(None)`。
`_handle_llm_error` / `before_iteration` / `on_error` / `_append_assistant_msg` /
`ctx_hash_id` 同步——**一行不动**（它们是可观测契约，不属于形状迁移）。

### 3.3 `tool_execution`

`metadata["tool_hashes"]` / `metadata["tool_result_msgs"]` 的**双写保持**（legacy 与
适配器都在读 metadata，删键是另一个决定），同时把它们放进 `output`。
`LoopActor` 从"读 metadata 再包 `StepResult`"退化成"直接转发"：

```python
# 前：tool_ctx = await loop._call_step(...); 从 metadata 抠出 tool_hashes 再包一层
# 后：return await loop._call_step(loop._strategy.tool_execution, tool_ctx)
```

### 3.4 `finalization`

改签名，`output=None`（metrics / claim validation / after_run 全是副作用，
无产物）。

### 3.5 `custom_steps.py` 的两个替换 step

`ClaimValidationFinalizationStep` / `NoOpToolExecutionStep` 是 `validator` /
`minimal` 两个 preset 的组成件（`validator.py:40`、`minimal.py:39`），**不是死代码**。
同样改签名：前者 `ok(None)`（它只写 `metadata`），后者 `ok(None)`（它只读 response）。

### 3.6 `pre_run` 接线（2026-11-12 裁决：接）

**装配链实测**（接线要动的三个点都已确认）：
`_inject_agent_loop(self._strategy, self)` 在 `__init__`（loop.py:478）绑定
`pre_run._loop`；`prepare=LoopPrepare(self)` 在 `_build_engine`（loop.py:1037）；
`LoopPrepare.__call__` 是 **sync**（adapters:162），由 `_prepare_state`
（sync，loop_engine.py:499）在 `run_stream` 的 `try:` **之前**调用
（loop_engine.py:300 vs 304）。

**改法**——`LoopPrepare` 从直调 `_prepare_run` 改为委托槽位：

```python
class LoopPrepare:
    def __call__(self, spec: Any) -> tuple[LoopContext, Any]:
        loop = self._loop
        # ctx 先建：step 要读 ctx.task/context/history 跑注入器与 ContextHub
        ctx = LoopContext(task=spec.task, context=spec.context, history=spec.history)
        # ── 接线：prepare 经由 strategy.pre_run（原 adapters:164 直调已删）──
        # **不走 _call_step**，两个硬理由：
        #   1. prepare 在引擎 try 之前，失败契约 = 崩溃（异常向上传播），
        #      _call_step 的 catch 会把它变成 fail-result——行为变化
        #   2. __call__ 是 sync，_call_step 是 async
        r = loop._strategy.pre_run.handle(None, ctx)   # DefaultPreRunStep.handle 同步
        if not r.success:
            raise RuntimeError(f"pre_run failed: {r.error}")   # 防御：同步路径到不了
        ctx.task = r.output["full_task"]   # ★ 原始 task → 前缀后 full_task（下游 git_commit 用）
        # 以下与现状逐行相同：subagent 计数重置 → hook_ctx → attach_pause
        # → metadata["raw_task"] → loop._skeleton_ctx = ctx
        ...
        return ctx, ctx.result
```

step 侧（`DefaultPreRunStep.handle`）在现职责上加一行：写 `ctx.task = full_task`
（step 自治：prepare 完成 ctx 即就绪）。`output` 四元组照 §2.3。

**三条边界声明**：

| 事项 | 裁定 |
|---|---|
| 失败契约 | **保持崩溃**。今天 `_prepare_run` 异常从 `LoopPrepare.__call__` → `_prepare_state` → `run_stream:300`（try 之前）向上抛，调用方见异常；接线后直调不经过 `_call_step` 的 catch，逐字保持。**原则：`_call_step` 的统一分派只覆盖循环体内的步骤**——prepare 在循环体外，失败语义本质不同（没有 result 对象可降级）|
| 双跑风险 | 原 ⚠（goal 块双份、检索 I/O 双份）变成 **grep 网**：接线后 `git grep -n "_prepare_run" -- loop_engine_adapters.py` 必须为 **0**（引擎路径唯一调用者 = pre_run step）；`loop.py` 保留 2 处（def:671 + legacy 内联:1125）。两路径各自单跑，互斥约定按路径成立 |
| 行为变化 | **唯一显式例外**：custom `pre_run` 从「静默忽略」变为「真的跑」（修 trap：`CustomStrategy(pre_run=X)` 今天被无视）。仓内零覆盖（explorer/validator/minimal 均未传 pre_run，实测）；默认路径字节级等价——step 只是 `_prepare_run` 的透传壳，`_prepare_run` 本体（loop.py:671-708：hypothesis / context 前缀 / **ContextHub 检索** / LoopResult / 初始 messages / 预算清零 / `loop_start` 事件）一行不动 |

**新测试（3 条）**：

1. **spy 网（行为级双跑网）**：custom strategy 传计数 `pre_run` → `arun` 一次
   → step 恰调 **1** 次，且 `loop_start` 事件恰 **1** 次（`_prepare_run:703` 发的——
   双跑的可观测信号就是它 ×2）。
2. **注入网（源码级）**：读 `loop_engine_adapters.py` 源码断言 `"_prepare_run"` 不在——
   有人把直调加回去，网就红（仓内先例：`test_regression_2026_08_05.py:84` 同型源码断言）。
3. **handle 单测**：ctx 字段写入 + `output` 四元组（full_task 前缀、result 新建、
   messages 初值、t0）。

等价性网兜底：`test_loop_engine_equivalence` 7 条钉消息序列（含系统提示与
注入前缀）——prepare 行为漂移会被它抓住。

---

## §4 8 个调用点的改造

**回填模式**（本批核心机制）：`_call_step` 的 catch 不再写 `ctx.should_stop`
（基线 loop.py:665-666 删除），崩溃信号改由**真正消费它的 4 个调用点**显式
回填进旧通道——下游条件逐字不变：

```python
r = await self._call_step(step, ctx)
if not r.success:                                            # 崩溃信号（新通道）
    ctx.should_stop = True                                   # ← 回填旧通道
    ctx.stop_reason = ctx.stop_reason or f"step_{step.name}" # 业务写优先，保文本
# ↓ 下游原条件（loop.py:1203 / 1282、adapters:94 / 148）逐字保留
```

`or` 的作用：llm_call 的**业务写**（`stop_reason="error"`，案例 2）优先于
回填文本——崩溃才落 `step_<name>`（案例 4），与今天 `_call_step` catch 的
取值逐字节一致。

| # | 位置 | 前 | 后 |
|---|---|---|---|
| 1 | `loop.py:1177` compaction | `comp_ctx = await _call_step(...)` → `messages = comp_ctx.messages` | `r = await _call_step(...)` → `messages = r.output["messages"]`；**不读 `r.success`**（吞没语义显式化，§2C 案例 5）|
| 2 | `loop.py:1200` llm_call | `if llm_ctx.should_stop: break` | **回填**（崩溃→`step_llm_call`；业务写 `"error"` 优先）→ 原条件**逐字保留**；`response = r.output` |
| 3 | `loop.py:1279` tool_execution | `if tool_ctx.should_stop: fire; break` | **回填**（`step_tool_execution`）→ 原条件逐字保留；`tool_hashes = r.output["tool_hashes"]`（metadata 双写保留）|
| 4 | `loop.py:1337` finalization | 忽略返回值 | `if not r.success: logger.warning(...)`（不中断——保持现状）|
| 5 | `adapters:61` compaction | `ctx.messages = comp_ctx.messages` | `ctx.messages = r.output["messages"]`；不读 success |
| 6 | `adapters:79` llm_call | `if llm_ctx.should_stop:` 传播（95/96）| **回填** → 传播块**逐字保留**；`ctx.response = r.output` |
| 7 | `adapters:144` tool_execution | `if tool_ctx.should_stop:` 传播（149/150）| **回填** → 传播块**逐字保留**；`LoopActor` 转发 `r.output` |
| 8 | `adapters:382` finalization | 忽略返回值 | `if not r.success: logger.warning(...)` |

**`messages` 回写一条不能漏**（`loop_engine_adapters.py:86-92` 的 V2 实测：
不回写 → 发给 provider 的消息序列缺 assistant 轮次 → MiniMax 400
`tool result's tool id(...) not found (2013)`）。B2-① 不改 `_make_strategy_ctx`
的浅拷贝语义，所以 4 个适配器调用点的回写**逐字保留**。

**为什么不是「轮询消失」**：`should_stop` 上混着两种信号——崩溃
（`_call_step` catch 写）与业务（`llm_call` 自己写 `"error"`/`"llm_none"`，
喂 `StopSignalDecider`）。前者死、后者活。删掉活的，空响应路径会烧到
max_iter 才停（§2C 案例 3 反证）。所以本批只删 catch 那一份，业务通道
原样保留到 B2-②/U6-2 再收编。

---

## §5 `protocol.py` 收敛：10 → 4

| 现有 Protocol | 处置 | 理由 |
|---|---|---|
| `Step`（name + should_run + execute）| **删** | `should_run` 零调用；`execute` 形状消失 |
| `PreRunStep` | **删** | 归入 `StepHandler`（`LoopStrategy` 注解改 `StepHandler`）|
| `LLMCallStep` | **删** | 同上 |
| `CompactionStep` | **删** | 同上 |
| `ToolExecutionStep` | **删** | 同上 |
| `FinalizationStep` | **删** | 同上 |
| `StopStep` | **留** | `evaluate(ctx) -> (bool, reason)`，`BudgetDecider` 在包它 |
| `ContinuationStep` | **留** | 同上，`GoalContinuationDecider` 在包它 |
| `ProgressStep` | **留** | `record_hash` / `is_no_progress`，`NoProgressDecider` 在包它 |
| `ResilienceStep` | **留** | `is_open`，`CircuitBreakerDecider` 在包它 |

**为什么四个判定型保留（2026-11-12 审视后修订的理由）**：

先说结论：**保留的决策不变，但原理由（"两条正交轴"）经不起代码证据检验，
已换掉**。用一个错误的原则支撑一个正确的决策，会让后来的决策跟着错——
比如有人拿"正交"去给 bool 槽位补 EXIT 支持（复制 approval_gate），
或者拿它永久抵制 decider 直连批。

**审视：'正交'的四条泄漏（每条都有代码证据）**

| # | 泄漏 | 证据 |
|---|---|---|
| 1 | **类型上不正交**——裁决器本来就继承步骤协议 | `class Decider(StepHandler)`（deciders.py:61）。同基座、同信号线（`StepResult`），差异只在 `output` 的载荷语义：`(Decision, reason)` vs 数据 |
| 2 | **信号上串联而非正交**——Step 的产出喂 Decider 的裁决 | `LoopReasoner` 把 llm_call 的结果写成 `ctx.should_stop`（adapters:94-96）→ `StopSignalDecider` 读它出裁决（deciders.py:181）；同型：`InjectorContinuationDecider` 读 `ctx.metadata["injector_continued"]`（adapters:125 写，deciders.py:278-280 读）。同一控制信号跨两次边界——正交轴不会互相喂 |
| 3 | **纯度契约已双边越线** | decider 侧：`NoProgressDecider.record(ctx, h)`（deciders.py:339-341）是 `handle` 之外的**第二个入口**，引擎按 `getattr(decider, "record", None)` 探测调用（loop_engine.py:394-398，批次 11 修 no-progress 失效时引入）——变异本身透传回 step 的窗口（progress.py:35），纯度没破，但"decider 只有一个入口"破了。槽位侧：`BudgetStopStep.evaluate` 写 `ctx.should_stop`（stop.py:56-57）——**两条消费路径都读不到这个写**（下条） |
| 4 | **值域不对等**——槽位是降级编码 | `(bool, reason)` 表达不了 CONTINUE/EXIT。翻译语义在 wrapper 不在槽位：`GoalContinuationDecider` bool→CONTINUE（deciders.py:255-258）；`CircuitBreakerDecider` `is_open`→**CONTINUE** "circuit_breaker_open"（deciders.py:300-302，跳过本轮 ACT 而非 STOP——这个语义判断槽位根本不知道）。EXIT 从不过槽位：审批 EXIT 走 `approval_gate` 闭包（modes.py:62-95） |

**修正后的表述**（替换 `loop-final-design` §1.1 的"两条正交轴"）：

> Step 与 Decider **共用同一基座（`StepHandler` / `StepResult`）与同一信号线**；
> 差异是**角色与纯度契约**，不是形状——
> 变形点：读累积态 → 产数据/副作用，失败 = `success=False`；
> 判定点：读 payload+ctx → 产 `(Decision, reason)`，契约只读（`record` 第二入口为既成事实）。
> 两者**串联而非正交**：变形的结果是判定的输入之一。
> 真实分工：**槽位持有状态**（progress 窗口、breaker 计数、budget limits 解析），
> **decider 持有语义**（谓词 → Decision 的翻译）。

**裁决（2026-11-12，维护者）**：不剔除 decider 家族，**重定位为「判定型 Step」**
——`class Decider(StepHandler)` 在类型上早已如此（deciders.py:61），保留是
承认既成事实。闸位机制与 4 个槽位 Protocol 一并保留（§5.1 形态 C）。

**保留 4 个 Protocol 的真实理由**（三条，均可执行验证）：

1. **双消费面有活消费者**：legacy 内联 4 处（loop.py:1230/1250/1264/1303-1304）
   + `deciders_for` 包装 4 处（modes.py:151/152/165/166/172）。
2. **删除 = 重接两处**：重写 `deciders_for` 的 4 个 wrapper（decider 直连）
   + legacy 4 处内联随 U6-2 消失。退役路径 = U6-2 之后的"decider 直连批"。
3. **批次 10 映射表测试**钉住 slot→gate 映射，动它要重跑映射验收。

**记账的判定层债务（三笔）**（保留形态下同样可修，与剔除决策正交；本批不动）：

1. `BudgetStopStep.evaluate` 的 `ctx.should_stop` 写 = **死副作用**：
   legacy 读返回值且 ctx 是 `_make_strategy_ctx` 的瞬态对象（写完即弃）；
   decider 读返回值且 STOP 短路使 `StopSignalDecider` 永远看不到这个写
   （`_run_gate_once` 首个非 PROCEED 即返回，loop_engine.py:238-246）；
   且 once exceeded 即终局，写不可能影响后续读。删除 = 零行为变化。
2. `NoProgressDecider.record` 第二入口：要么修订 decider 契约措辞承认它
   （推荐——变异已透传回 step，decider 对象本身无状态），要么把窗口挪回
   step 由引擎直调 step。
3. **隔离粒度不对称**（2026-11-13「decider 也是 step」追问发现）：step 崩溃经
   `_call_step` **逐个**隔离成 `fail`——调用方可以吞掉继续（§2C 案例 5）；
   decider 崩溃**无逐个隔离**——闸位 runner 不 catch（`_run_gate_once`
   loop_engine.py:231-246 无 try），直接穿透引擎级兜底（`run_stream` except
   → error 事件 + `finished_reason="error"`），整轮必断。两个自含 decider 对
   **注入依赖**自行 try/except（ExternalTerminationDecider deciders.py:158-160、
   NoProgressDecider 的 gate deciders.py:353-356），但 `decide` 本体没有。
   两者最终都经 FINALIZE 漏斗，差别是**可恢复性**（step fail 可被吞、decider
   崩必断）。处置二选一：契约明确「decider 崩溃 = 引擎级 error 出口」（推荐，
   零代码变化只补措辞），或给闸位 runner 加逐个隔离（行为变更，单独决策）。

### 5.1 追问：剔除 Decider 是否功能完整（2026-11-12）

**结论：可以，但要分清剔的是什么。** 功能完整的下限 = 下表 10 个判定点
+ 8 种 `finished_reason` + HITL 审批交互。这些在任何形态都必须存在；
剔除 Decider 改变的是它们的**宿主**，不是判定本身。实测佐证：**legacy
就是"无 Decider"形态**——10 个判定全部内联在 `_run_loop_core`
（246 行，noqa C901），功能完整。

**decider 家族解剖**（deciders.py 363 行 + modes.py 179 行）：

| 成员 | 型 | 实质 |
|---|---|---|
| MaxIterations / StopSignal / TextFinal / InjectorContinuation / ExternalTermination | **自含 ×5** | 判定逻辑就在 decider 里，不包槽位 |
| Budget / GoalContinuation / CircuitBreaker / NoProgress | **包装 ×4** | 真逻辑在 strategy 槽位，decider 只做 bool→Decision 翻译 |
| `_CombinationDecider`（`and_`/`or_`）| **死** | 零生产调用者（只有 `test_decider_composition.py`）|

**三种"剔除"形态**：

| 形态 | 做法 | 功能 | 丢 / 得 | 净行数 |
|---|---|---|---|---|
| A 整体剔除 | decider 层 + 闸位机制全删，10 个判定内联回 `run_stream` | 完整（legacy 即此形态）| **丢**：闸位数据化（加停条件 = 改引擎本体）、Decision 4 值漏斗（CONTINUE/EXIT 退化成 `continue`/`break` + `_ending_emitted` 标志散落实现）、HITL 挂起的现成的家；**得**：少一层间接 | ≈ −500（但 run_stream 长回 ~100 行内联，引擎与判定重新耦合）|
| **B 剔包装、留自含（推荐）** | 4 个 WRAP decider + 4 个槽位 Protocol 退役；5 个 SELF decider 直连真实状态源；闸位骨架与 Decision 4 值保留 | 完整且更直接（"为什么停"少一层间接）| **得**：清掉本节两笔槽位债、组合子死代码一并退役；**丢**：slot→gate 映射表测试（批次 10）需重写 | ≈ −150~200 |
| C 不动（现状）| — | 完整 | 维持双消费面（legacy 内联 + decider 包装）与两笔债 | 0 |

**支撑形态 B 的两个实测事实**：

1. `and_` / `or_` 零生产调用者 → `_CombinationDecider` 在形态 B 一并退役。
2. `ctx.pause(` 生产调用者 = **0**（只有 `pause.py:163` 的 docstring 示例与
   `test_loop_pause_resume.py`）——H1 挂起机制是"接好线等第一个 caller"的
   基础设施，不是承重墙。形态 A 也不丢**现有**功能，丢的是未来挂起点的
   现成的家（上位设计 §6 已列"审批迁移到 pause/resume = U6 后可选批"）。

**10 个判定点（剔除的下限，任何形态都必须存在）**：

| # | 判定 | 现宿主 | 落点 |
|---|---|---|---|
| 1 | 迭代上限 | `MaxIterationsDecider`（precheck）| `finished_reason="max_iter"` |
| 2 | 预算 tokens/cost/time | `BudgetDecider` ← `BudgetStopStep`（precheck + after_reason）| `"budget"` |
| 3 | 熔断 open | `CircuitBreakerDecider` ← `ResilienceStep`（precheck，批次 10 从 pre_act 并入）| CONTINUE（跳过本轮 ACT，无 finished_reason）|
| 4 | 外部 halt | `ExternalTerminationDecider`（precheck）| `"exit"` |
| 5 | LLM 错误 / 空响应 | `StopSignalDecider` 读 `ctx.should_stop`（after_reason）| `stop_reason="error"/"llm_none"`，`finished_reason="error"` |
| 6 | goal 续跑 | `GoalContinuationDecider` ← `ContinuationStep`（after_reason）| CONTINUE |
| 7 | injector 续跑 | `InjectorContinuationDecider` 读 metadata（after_reason）| CONTINUE |
| 8 | 文本终答 | `TextFinalDecider`（after_reason）| `"stop"` |
| 9 | no-progress → 审批 | `NoProgressDecider` + `approval_gate`（after_act）| `"exit"`（`user_rejected` / `approval_timeout_rejected`）|
| 10 | **迭代超时** | **不在 decider 家族**——`_with_iteration_timeout`（LLM 调用包装层，loop_lifecycle.py:30）| `"timeout"` |

第 10 行是本追问的附加发现：**判定逻辑的宿主本来就分散**（decider 家族 +
调用包装层），"decider 层 = 全部判定"不成立。这既说明形态 A 没想象中
灾难（判定本来就有一半不在闸位里），也说明形态 B 剔掉的确实只是包装税。

**裁决（2026-11-12，维护者）**：**不剔除——形态 C（保留）**，重定位为
「判定型 Step」（类型事实：`class Decider(StepHandler)`，deciders.py:61）。
形态 A / B 归档为未采纳的备选；「decider 直连批」从"退役路径"降级为
**未排期的可选简化批**。本节的两笔债与剔除决策正交，保留形态下仍可单独修。

`protocol.py` 预计 161 → ~90 行。

---

## §6 边界（本批明确不做）

| 不做 | 理由 |
|---|---|
| 事件搬进 `StepResult.events` | B2-②；本批 `events` 恒空。搬事件要重排 `_emit_iter_start` 与 `after_iteration` 的相对顺序，属行为变更 |
| 删 `_run_loop_core` / 引擎开关 | U6-2 |
| 删 `loop_tools.py` 的 `async_mode`（10 处）| 同一批语义，但它是工具核心不是 step；U6-2 与 legacy 一起删 |
| legacy 内联 `_prepare_run`（loop.py:1125）| U6-2 与 legacy 一起删；接线只动引擎路径（§3.6），两路径互斥按路径成立 |
| 接线判定型槽位 / decider 直连 | §5：4 个槽位是 decider 的有状态底物 + legacy 消费面，退役归独立批 |
| 改 `_make_strategy_ctx` 浅拷贝 | 波及 9 个构建点；V2 已用显式回写绕过，改它是另一批 |
| `and_` 组合子 / `pre_act` 空闸 | backlog（P1 / P2），与形状无关 |
| 改名 `_call_step` | 纯噪音；归 U6-2 命名收尾 |
| 动 4 个判定型槽位的接口 | §5 |

---

## §7 验收断言（可 grep，不靠"跑绿了"）

| # | 断言 | 检查方式 |
|---|---|---|
| 1 | `src/` 里 `async_mode` 恰好 **18** 处 = `loop.py`(8) + `loop_tools.py`(10)，别处为 0 | `git grep -c async_mode -- src` |
| 2 | 7 个 step 类 + `step_adapters.py` 里 `async_mode` = **0** | `git grep -n async_mode -- src/agentnodes/kernel/agent/strategy src/agentnodes/kernel/agent/step_adapters.py` → 空 |
| 3 | `_call_step` 签名无 `async_mode` | `git grep -n "async def _call_step"` |
| 4 | `strategy/` 下 `def execute(` = **0**（基线 13 = 7 实现 + 6 Protocol 声明）；7 个 step 类各有 `handle` | `git grep -n "def execute\|def handle" -- src/.../strategy` |
| 5 | `protocol.py` 里 `(Protocol)` 恰好 **4** 个 | `git grep -c "(Protocol):" -- .../protocol.py` |
| 6 | `def should_run` = **0** | `git grep -n "def should_run" -- src` |
| 7 | `should_stop` 的**唯一删除点** = `_call_step` 体内的 catch 写（基线 loop.py:665）。4 个读 stop 通道的调用点（llm ×2 / tool ×2）新增回填（`should_stop=True` + `stop_reason = stop_reason or f"step_{name}"`）；compaction / finalization 4 个调用点显式不读；**逐字保留**：下游条件（loop.py:1203/1282、adapters:94/148）、传播写（adapters:95/96/98/99/149/150）、llm_call 业务写（llm_call.py:63/67-68）——StopSignalDecider 通道零变化 | 源码断言：`_call_step` 函数体内 `should_stop` = **0**（基线 1）；`grep -c 'stop_reason or f"step_'` 恰 **4**（回填）；`git grep -n should_stop -- .../steps/llm_call.py` 恰 **2**（业务写保留）|
| 8 | `LegacyStepAdapter` = **0** | `git grep -n LegacyStepAdapter -- src` |
| 9 | 两引擎等价性 7 条全绿 | `tests/test_loop_engine_equivalence.py` |
| 10 | 9 个出口 × 6 断言全绿 | `tests/test_loop_exit_net.py` |
| 11 | `finished_reason` 的 **8** 种字面量一个不减（实测：`approval_timeout_rejected` / `budget` / `error` / `exit` / `max_iter` / `stop` / `timeout` / `user_rejected`）| 全量套件（~40 条断言在钉它）+ `git grep -E 'finished_reason\s*=\s*"[a-z_]+"'` |
| 12 | `scripts\batch_gate.cmd` 8 步全绿 | 批末 |
| 13 | **pre_run 接线双跑网**：`_prepare_run(`（带括号 = 代码调用，docstring 的 `` `_prepare_run` `` 无括号天然排除）恰 **3** 处 = `loop.py` 2（def:671 + legacy 内联:1125）+ `pre_run.py` 1（step 委托:41）；`loop_engine_adapters.py` = **0**。基线 4 处（adapters:164 接线时删） | `git grep -n "_prepare_run(" -- src/agentnodes/kernel/agent` + 源码注入测试（§3.6 测试 2）|

**第 1 条是硬指标**：如果迁移完 `async_mode` 还剩 40 处，说明只是换了层皮。

### 7.1 迁移前基线实测（2026-11-12 `7e68ac77`）

12 条逐条跑过，全部与上表"基线"值一致（第 13 条是本批新增网，无基线）：

```
【1】src async_mode = 50，12 文件
【2】strategy/ = 17 + step_adapters.py = 4 = 21
【3】_call_step 定义 loop.py:649，签名含 async_mode
【4】strategy/ def execute = 13
【5】protocol.py (Protocol) = 10
【6】def should_run = 4（1 声明 + 3 实现）
【7】should_stop 代码行 11（loop.py 6 + adapters 5）
【8】LegacyStepAdapter = 4（1 类定义 + 1 __all__ + 2 docstring）
【9】test_loop_engine_equivalence.py 存在
【10】test_loop_exit_net.py 存在
【11】finished_reason 8 种：approval_timeout_rejected / budget / error /
     exit / max_iter / stop / timeout / user_rejected
【12】scripts/batch_gate.cmd 存在
【13-基线】`_prepare_run(` 代码调用 4 处：loop.py 2（def:671 + legacy 内联:1125）
     + adapters:164 + pre_run.py:41；接线后 = 3（adapters 归零）
```

复现脚本：`tmp/probe/assertion_baseline2.py`（临时目录，不入库）。

---

## §8 测试迁移（6 文件 / 42 处 `async_mode`）与防假绿

| 文件 | `step.execute(` | 处置 |
|---|---|---|
| `test_compaction_step_migration.py` | 6 | 逐条改 `handle` + 从 `output` 取断言；`test_should_run_returns_true`（L110）**删除**（§1.5）|
| `test_llm_finalization_step_migration.py` | 7 | 同上；`_FakeLoop._get_response` 去掉 `async_mode` 形参（L49）|
| `test_tool_execution_step_hooks.py` | 8 | 同上；`assert "tool_hashes" not in ctx.metadata`（L85）改成断言 `output` 的键为空 |
| `test_loop_strategy_three_presets.py` | 3 | 同上（2 个 custom step + 1 个 noop）|
| `test_loop_v2_decision_migration.py` | 2 | **不是真 step**：这两条在测试内部**复制了一份 `_call_step`**（L137/L169）。改为直接调 `AgentLoop._call_step`，否则测的是副本（R-009 同型缺陷）|
| `test_core_types.py` | 1 | `_Broken` 桩（L195）改 `handle`；`LegacyStepAdapter` 三条用例改写为「`DefaultLLMCallStep` 直接进 Pipeline」——迁移后它本身就是 `StepHandler`，互操作证明反而更强 |
| `test_loop_strategy_abstraction.py` | 0（但断言 Protocol）| `isinstance(step, PreRunStep)` 等 9 条（L74-85）→ `isinstance(step, StepHandler)` |
| `tests/test_pre_run_wired.py`（新）| — | §3.6 三条：spy 恰 1 次 + `loop_start` 恰 1 次；adapters 源码无 `_prepare_run`（注入网）；`handle` 单测（ctx 写 + output 四元组）|

### 三条防假绿规则（R-011 教训直接落地）

R-011 记录过：**机械批量替换测试代码，3 类错，2 类症状是"假绿"**。本批是同一风险面。

1. **禁止 `sed` 级批量替换**。逐文件改，每改一个文件立刻单跑该文件。
2. **删断言要写明理由**。像 `assert out is ctx`（`compaction:119`、`finalization:168`）
   这种迁移后必然失效的，改写成 `assert r.output["messages"] == [...]` 或
   `assert r.success is True`——**不是**直接删成只剩 `r.success`（那是恒真断言）。
3. **每个文件保留至少一条负向断言**。`fail` 路径必须有测试真的让它抛异常并断言
   `success is False` + `error` 含 step 名，否则"错误隔离"退化成没测。

---

## §9 风险

| 风险 | 后果 | 缓解 |
|---|---|---|
| 半迁移状态：某 step 返回 `StepResult` 但调用方还在读 `ctx.should_stop` | `should_stop` 恒 False → **静默继续跑**，症状是"多跑几轮"而非报错（最坏的一类）| 单 commit 原子提交；§7 第 7 条 grep 为证 |
| `messages` 回写漏一处 | provider 400（MiniMax 严格校验），多数 provider 不报 | §4 明文列 4 处回写；`test_loop_engine_equivalence` 7 条钉消息序列 |
| `stop_reason` 文本漂移（`step_<name>` / `llm_none` / `error`）| 下游按 `finished_reason` 分流的地方行为变 | §2.4 明文钉三处取值；`_handle_llm_error` 不动 |
| `should_run` 删除后第三方 strategy 静默失效 | 无——它本来就没生效 | §1.5 证据 |
| `LegacyStepAdapter` 删除波及外部 | 已核：`kernel/agent/__init__.py` **未导出**该类；`src/` 内只有 3 处 docstring 提及 | 提交前 `git grep LegacyStepAdapter` 应只剩 `_core_types.py` / `protocol.py` 的历史注释（一并清理）|
| 事件顺序被动 | 本批不动 `_emit` 调用位置，理论上零变化；但 `finally` 的顺序若被重排会变 | `test_loop_exit_net` + `test_no_progress_event_sequence` 钉绝对序列 |
| **pre_run 接线的显式行为例外**：custom `pre_run` 从静默忽略变为生效 | 若外部用户传了一个期望被忽略的 pre_run step，它现在真的跑（可能在每次 run 前产生副作用）| 仓内零覆盖（explorer/validator/minimal 实测均未传）；默认路径字节级等价（step = `_prepare_run` 透传壳）；spy 网钉恰 1 次 |
| 接线后 prepare 异常被 `_call_step` 捕获 | 崩溃 → fail-result，失败契约变（调用方从"见异常"变"见 result"）| §3.6 硬裁定：**不走 `_call_step`**，直调保持异常传播；§7 第 13 条 + 等价网兜底 |

---

## §10 回滚

- **范围**：单 commit，4 组源文件（`strategy/steps/*` 5、`strategy/custom_steps.py`、
  `strategy/protocol.py` + `loop_strategy.py`、`loop.py` + `loop_engine_adapters.py`
  + `step_adapters.py` 删除）+ 7 个既有测试文件 + 1 个新测试文件
  （`test_pre_run_wired.py`）。pre_run 接线在 `loop_engine_adapters.py` 与
  `pre_run.py` 内，随同组回滚。
- **无数据/配置迁移**：不涉及 DB、`config.yaml`、env、trace 格式。
- **回滚动作**：`git revert <sha>`。形状变更部分 revert 后无需重跑迁移脚本；
  唯一注意：revert 会把 custom `pre_run` 重新变回「静默忽略」（§3.6 例外条目
  一并消失）——这是预期行为，不是回滚事故。
- **部分回滚**：不支持（半迁移会产生 §9 第 1 条的静默错误），要么整批要么不做。

---

## 附 · 与本设计的后续接口

| 后续批 | 它消费本批的什么 |
|---|---|
| **B2-②** | `StepResult.events` 从"恒空"变成唯一事件通道；`_emit` 副作用搬进 step 的 return。本批保证 `events` 字段已在类型上就位、`StreamingHandler` 未被触动 |
| **U6-2** | 删 `_run_loop_core`（含 8 处 `async_mode`）与 `loop_tools.py` 的 10 处。本批把 step 层清空后，U6-2 的删除面就是纯粹的两个文件。pre_run.py 的 ⚠ 互斥警告随 legacy 内联一起删除 |
| **decider 直连批** | 4 个判定型槽位退役（deciders_for 直连真实状态源）。本批保留它们的 Protocol 就是为了那一批有形状可依；§5 记账的两笔槽位债务（`BudgetStopStep` 死副作用、`NoProgressDecider.record` 第二入口）在该批一并处置 |

---

## 附 B · 统一进度对照（2026-11-13 校准）

回答「现在是不是统一引擎了」：**主路径是（生产流量 100% 走 LoopEngine），终态还差 4 块**。
对照 `loop-final-design-20261112.md` §9 的 9 个维度：

| 维度（终态定义） | 现状 | 批次 |
|---|---|---|
| 入口：金字塔 3 个（run_stream → arun → run）| ✅ 完成 | U5.5 |
| 外部终止：ExternalTerminationDecider | ✅ 完成 | U4 |
| 预算：停 + `budget_exhausted` 信号 | ✅ 完成 | U2 |
| 循环体：1 个（run_stream）| 🔶 **默认且唯一生产路径**；legacy `_run_loop_core` 仍在树内，仅显式回退可达（`engine="legacy"` / `AGENTNODES_ENGINE=legacy`）| U6-2 删 |
| 步骤协议：`handle/stream(input, ctx) → StepResult` | ❌ 7 类仍是 `execute(ctx, async_mode)`（两引擎**共享对象**但协议是旧形状）| **B2-① = 本文档** |
| 事件：步骤产出 → 分发器三写 | ❌ 仍是 `_emit` 副作用 + 事件桥（U3 双通道）| B2-② |
| async：全 async、`maybe_await` 一处收口 | 🔶 生产恒 async（实测），但 `async_mode` 形参 50 处仍在 | B2-① 消 32、U6-2 消 18 |
| HITL：pause/resume + 既有两层保留 | 🔶 机制落地（H1）但 `ctx.pause` 零生产调用者；审批仍走旧两层 | U6 后可选批 |
| `StopPolicy` 处置 | ❌ 未动 | U6 待定 |

**B2-① 的位置**：它不是"引擎统一"（那在批次 12 S1 + U5.5 已完成），是**协议统一**
——两引擎今天共享同一批 step 对象但读旧协议；协议统一后，U6-2 删 legacy 时
两个循环体不再共享任何双形状对象（R-015 的删除前置之一）。

**随时可复核**：`config audit-legacy-engine`（legacy_audit.py，纯静态）数 legacy 残留。
