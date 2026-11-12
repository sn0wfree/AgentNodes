# 判定层崩溃隔离（B2-①b 前置小批 · 2026-11-13）

> **上位**：`step-protocol-migration-20261112.md` §5 判定层债务③。
> **性质**：**行为变更小批**（4 条显式差异，§4）；纯修改、**零删除**
> （R-020 不适用——没有删除工序；R-007 三件套照常）。
> **裁决来源**：2026-11-13 维护者「这个能修吗」→ 采纳「修（角色化 fail-safe）」，
> 否决备选「不修只补措辞」（留下归因模糊 + 续跑型崩误杀整轮）。

---

## §0 结论摘要

| # | 事 | 数字（实测） |
|---|---|---|
| 1 | 闸位 runner 逐个隔离 | `_run_gate_once`（loop_engine.py:231-246）加 try——decider 本体崩溃不再穿透引擎级兜底 |
| 2 | record 第二入口防护 | loop_engine.py:393-398 喂窗循环加防护（只修 `_run_gate_once` 盖不住它） |
| 3 | fail-safe 方向 | 角色化：守护型 6 个 `STOP`（默认）/ 放行型 2 个 `PROCEED` |
| 4 | 行为变化 | **4 条**（§4）——非零变化批 |
| 5 | 删除 | **0** |

---

## §1 现状实测

**闸位 runner 无隔离**（loop_engine.py:238-241）：

```python
for decider in self._deciders.get(gate, ()):
    r = decider.handle(payload, state)          # ← 无 try
    if not isinstance(r, StepResult):
        r = await r
```

decider 本体抛异常 → 穿透 `_run_gate` → `run_stream` 外层 try
（loop_engine.py:451-456）→ `result.error`、`stop_reason="error"`、error 事件
——**整轮必断，且归因只剩 "error"**（不知道哪个裁判崩了）。对比：step 崩溃经
`_call_step` 逐个隔离成 `fail`，调用方可以吞掉继续（B2-① 文档 §2C 案例 5）。

**record 第二入口无防护**（loop_engine.py:393-398）：`record_fn(state, h)` 裸调，
喂窗崩溃同样穿透。

**注入依赖已有自隔离**（两层正交，§2.3）：`ExternalTerminationDecider` 对
`halt_check`（deciders.py:158-160，失败 → PROCEED）、`NoProgressDecider` 对
`approval_gate`（deciders.py:353-356，失败 → EXIT 保守退出）。它们管**依赖**，
不管 decider **本体**。

**budget 子串映射**（loop_engine.py:320/350）：`if "budget" in (reason or "")`。
正常预算 stop 的 reason 是 `budget:tokens` / `budget:cost` / `budget:time`
（budget.py 实测）——都含 "budget"，映射正确。但崩溃裁决若叫
`decider_budget`，**也会命中**——崩溃 ≠ 超支，必须守卫（§4 差异④）。

**引擎级兜底兜得住**：`run_stream` except 后仍走 FINALIZE 漏斗
（loop_engine.py:458+）——trace 关闭、done 事件都在。隔离改造不动这条漏斗。

---

## §2 目标契约

### 2.1 `Decider.crash_policy`（契约补措辞 = 债务③的处置落点）

```python
class Decider(StepHandler):
    #: 崩溃 fail-safe 方向（B2-①b）：闸位 runner 对 decider 本体异常逐个隔离，
    #: 按本属性合成裁决 StepResult.ok((crash_policy, f"decider_{name}"))。
    #: - 守护型（默认 STOP）：守护崩了还放行 = 失控比中断危险
    #: - 放行型（续跑类覆盖 PROCEED）：裁判崩了还 CONTINUE = 无限续跑；
    #:   放行由 TextFinal / max_iter 兜底（有界）
    #: 注入依赖的降级方向是各子类既有语义（halt 失败→PROCEED、gate 失败→EXIT），
    #: 与本属性正交——它们管依赖，本属性管本体。
    crash_policy: Decision = Decision.STOP
```

### 2.2 `_run_gate_once` 逐个隔离

```python
for decider in self._deciders.get(gate, ()):
    try:
        r = decider.handle(payload, state)
        if not isinstance(r, StepResult):
            r = await r
    except Exception as exc:                      # 逐 decider 隔离
        logger.warning(...)
        self._emit_decider_error(gate, decider, exc)
        dname = getattr(decider, "name", type(decider).__name__)
        r = StepResult.ok((getattr(decider, "crash_policy", Decision.STOP),
                           f"decider_{dname}"))
    decision, reason = r.output
```

`_emit_decider_error(gate, decider, exc)`：与 `_emit_pause`（loop_engine.py:264-286）
同款取 loop（`event_source()`），`loop._trace_and_emit("decider_error",
{decider, gate, error})`——trace + 事件总线一次到位；emit 失败不阻断闸位。

### 2.3 record 循环防护

`record_fn(state, h)` 逐 hash 包 try——喂窗失败 → warn + `decider_error` 事件
（gate 标 `"after_act.record"`）→ **继续喂下一个 hash**。语义：no-progress
保护降级（窗口缺 hash，可能漏判），但不阻断循环、可观测。

---

## §3 fail-safe 方向表

| 角色 | 成员 | 崩溃裁决 | 理由 |
|---|---|---|---|
| 守护型（默认 `STOP`） | MaxIterations / Budget / CircuitBreaker / ExternalTermination / StopSignal / NoProgress / 组合器 | `(STOP, "decider_<name>")` | 守护崩了放行 = 预算不封顶、熔断失效、no-progress 失灵——失控比中断危险 |
| 放行型（覆盖 `PROCEED`） | GoalContinuation / InjectorContinuation | `(PROCEED, "decider_<name>")` | 续跑裁判崩了还 CONTINUE = 无限续跑；放行由 TextFinal 终答 / max_iter 兜底（有界） |

注：TextFinal / StopSignal 保持默认 STOP——TextFinal 崩溃时 after_reason 的
STOP 分支仍会写 `result.answer`（loop_engine.py:359-363），答案不丢。

---

## §4 行为变化清单（4 条，全部显式）

| # | 变化 | 前 | 后 |
|---|---|---|---|
| ① | 归因 | decider 崩 → `stop_reason="error"` + `result.error=str(exc)`，不知哪个裁判崩 | `stop_reason="decider_<name>"`；`result.error` 不再被写（gate STOP 路径本就不写）；exc 文本进 `decider_error` 事件 + warning 日志 |
| ② | 续跑型崩溃 | 整轮 error 出口 | 放行 → TextFinal 终答或 max_iter 兜底（有界） |
| ③ | record 崩 | 整轮 error 出口 | 降级继续（窗口缺 hash，可观测） |
| ④ | budget 映射守卫 | （若不守卫）`decider_budget` 命中 `"budget" in reason` → 误标 `finished_reason="budget"` | 两个 STOP 分支加 `and not reason.startswith("decider_")`——崩溃 ≠ 超支 |

`finished_reason` 沿用既有闸位 STOP 语义：守护型崩溃落缺省 `"stop"`（与
StopSignal 传播的 tool 崩溃等既有闸位 STOP 一致），不新增枚举值。

---

## §5 落地清单

| 文件 | 动作 |
|---|---|
| `deciders.py` | `Decider.crash_policy` 类属性（默认 STOP，docstring 契约）；`GoalContinuationDecider` / `InjectorContinuationDecider` 覆盖 `PROCEED` |
| `loop_engine.py` | `_run_gate_once` 隔离；新增 `_emit_decider_error`；record 循环防护；两个 STOP 分支的 budget 映射守卫 |
| `tests/test_decider_crash_isolation.py` | 新网（§6 六条） |
| 架构图 | `sync_architecture_diagram.py --write`（src 行数变化） |

---

## §6 验收断言（注入网，R-019）

| # | 断言 | 层 |
|---|---|---|
| 1 | 守护型崩 → `(STOP, "decider_boom")`，不穿透 | `_run_gate_once` 单测 |
| 2 | 放行型崩 → PROCEED → **后续 decider 仍被咨询**（gate=[崩的续跑型, TextFinal] → `"final_text"`） | 单测 |
| 3 | 崩溃发 `decider_error` 事件（decider/gate/error 三字段） | 单测（stub loop） |
| 4 | record 崩 → 循环完成 `finished_reason="stop"`、`error=None` | 集成（progress.record_hash 注入必崩） |
| 5 | budget 槽崩 → `stop_reason="decider_budget"` 且 `finished_reason != "budget"`（映射守卫）、`error=None`、trace 关闭 | 集成 |
| 6 | goal 槽崩 → 文本终答保留（`answer` 不丢、`"final_text"`） | 集成 |
| 7 | 非崩溃路径零变化 | `test_loop_engine_equivalence` 7 条 + `test_decider_composition` 全绿 |

---

## §7 与 B2-① 的关系

文件面**不相交**：本批动 `deciders.py` + `loop_engine.py`；B2-① 动 `loop.py` /
`loop_engine_adapters.py` / `strategy/steps/*` / `protocol.py`。零耦合。
排 B2-① **之前**：闸位 runner 先加固，B2-① 迁移期间任何 step/decider 交互
回归都会被同一张注入网照亮；避免 B2-① 中途插批。

---

## §8 回滚

单 commit revert；无数据/配置迁移。行为变化 4 条全部随 revert 消失。
