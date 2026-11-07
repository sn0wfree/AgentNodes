# 批次 9 设计 · B1 协议统一（第一阶段架构验收 1/2）

> **上位设计**：`unified-loop-adoption-eval-20261107.md` §3 B1。
> **性质**：纯增量，行为零变化——现有 9 个 Protocol / steps / mixins
> **一行不改**，新接口与适配器并行落地。验收 = 全量 + capability_smoke
> + 一个「legacy step 经适配器进 Pipeline」的互操作证明。

---

## §0 结论摘要

| 动作 | 文件 | 内容 |
|---|---|---|
| 新增 | `kernel/agent/_core_types.py` | StepResult / StepHandler / StreamingHandler / Pipeline / maybe_await（自 reference 移植，签名定为 `handle(input, ctx)`）|
| 新增 | `kernel/agent/step_adapters.py` | `LegacyStepAdapter`（旧 `execute(ctx, async_mode)` 步骤 → 新 StepHandler）+ `FnStreamingHandler`（async-gen → StreamingHandler）|
| 文档 | `strategy/protocol.py` | 模块 docstring 指向新接口（**无运行时改动**）|
| 新增 | `tests/test_core_types.py` | 四原语单测 + 与真实 legacy step（`DefaultLLMCallStep`）的互操作 |

---

## §1 签名裁决（与 llmwikify 的一个有意差异）

llmwikify：`handle(input, spec, ctx)`——`spec` 是它的 BaseSpec 概念。
AgentNodes **B1 时点没有 spec**（LoopSpec 是 B3 产出），且现有
`LoopContext` 已携带全部状态。**裁决：`handle(input, ctx)`**——
B3 引入 LoopSpec 时 spec 内容走 ctx，签名不再变。

配套差异：
- llmwikify 的 `handle` 是 `async def`（恒 await）；AgentNodes 大量步骤是
  同步的 → **`handle` 可返回 `StepResult | Awaitable[StepResult]`**，
  `maybe_await` 统一（`inspect.isawaitable` 判定——与 `_call_step`
  loop.py:507 的既有手法一致）
- `maybe_await` 落在 `_core_types.py` 内，docstring 标注：与
  `runtime/sync_bridge.py`（loop.py:63 提到的第二份拷贝）是待统一项，
  B3 收口

## §2 适配器（连接新旧世界的桥）

### 2.1 `LegacyStepAdapter(StepHandler)`

```
 wraps: 任何带 execute(ctx, *, async_mode) 的步骤（现有 9 个 steps/ 实现）
 handle(input, ctx):
     try: out = step.execute(ctx, async_mode=True)
          await if awaitable
          return StepResult.ok(out)
     except Exception as exc:
          return StepResult.fail(str(exc))
```

**语义映射（诚实标注）**：legacy 步骤用 `ctx.should_stop` 副作用表达
失败/停止，不抛异常——适配器**只映射异常**；`should_stop` 的读取归
B2 的 deciders（那正是 B2 要把副作用收编为返回值的原因）。

### 2.2 `FnStreamingHandler(StreamingHandler)`

包装一个 async 生成器函数（B2/B3 接 `_astream_chat` 时的现成壳）：
yield 的事件透传，`StepResult` 透传——契约与 llmwikify 一致
（最后一个 yield 是 StepResult）。

## §3 互操作证明（验收核心）

测试链：`Pipeline(LegacyStepAdapter(DefaultLLMCallStep()), NewStyleStep())`
——真实 legacy step（loop=None 路径返回原 ctx）+ 新式 handler 同管道串联。
这一条过了，"换底座不换职责"就有了可执行证据。

## §4 明确不做（本批）

- 不动 `loop.py` / mixins / `loop_stream.py`（B2/B3）
- 不动 9 个 steps/ 的接口（适配器即接缝）
- 不接 llmwikify 反向依赖（单独一批，有生产流量）
- 不引入 LoopSpec（B3）
