# 隔离执行器深化（候选 1+2）R-014 方案

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **前置**：B2.4 全阶段已验收（`17f1d76f`，开关默认关 + allowlist 空）；
> 架构复审 `architecture-review-20261120-121201.html` 候选 1+2，grilling 12 项决策达成共识。
> **范围**：把候选 1（事件 schema 收口）与候选 2（编排下沉）合并为一个深模块
> `IsolatedToolExecutor`；顺带收候选 3（结果信封）、候选 5（测试旋钮）。
> 词汇：`GLOSSARY.md` 已新增 `IsolatedToolExecutor` 词条。

## 0. 目标与非目标

**目标**：把散在 `loop/tools.py` 编排分支 + `_isolated_exec.py` helper + 两处
事件字符串 + 测试旋钮里的隔离执行知识，收进一个 **deep module**——单一
interface、一处测试面、一处事件契约。

**非目标**：① 不改 `isolation.py` / worker 的已验收协议（信封 / per-call id /
codec / 毒化）；② 不改开关默认关 / allowlist 空的生产契约；③ 不接 B2.5 试点；
④ 不动 E5 终态语义本身（只重定位 outcome 记录的调用点）。

## 1. 现状证据（复审 + grilling 取证）

- `_isolated_exec.py:150-157` shadow 事件用 `kind` 键 + `isolation.shadow_decision`；
  `loop/tools.py:198-199` terminal 用 `_trace_and_emit` 首参 `isolation_terminal`
  ——两种命名、两种 payload、两模块，均未进 `EventType`（`event_v2.py:69-110`）。
- `loop/tools.py:224-267` 隔离分支内联五件事（决策 / 预算 / 权限 / run-arun /
  outcome），`_emit_isolation_outcome` 在 `loop/tools.py:102-134` 又算 declared /
  terminal / finalize ——编排跨两文件；`async_mode` 参数在函数体零读取。
- `_isolated_exec.py:164-182` `IsolatedOutcome` 6 字段 + 派生 `result_rejected`
  是 `IsolationResult` 7 字段逐字抄写；`platform` 生产零读、`result_rejected`
  仅测试引用（浅映射，deletion test：删掉无行为丢失）。
- `should_isolate(enabled=/platform=/allowlist=)` 三旋钮主要给测试注入；
  `test_isolated_exec_wiring.py` 直读 `channel._queue` / 断言 `kw["timeout_s"]`
  ——私有管线被钉进测试（违反 "interface is the test surface"）。
- `EventType` 有 `_ALL_EVENT_TYPES` + `is_known_event_type` 校验
  （`event_v2.py`），typo FAIL LOUDLY 是明示纪律。

## 2. 设计（12 项决策落位）

| # | 决策 | 落位 |
|---|---|---|
| Q1 | 加深现有 `_isolated_exec.py`（不新建文件） | 类名 `IsolatedToolExecutor`，路径不变 |
| Q2 | 两类事件进 `EventType`，深模块统一构造 | `ISOLATION_SHADOW_DECISION` / `ISOLATION_TERMINAL`，入 `_ALL_EVENT_TYPES`；观测事件不登记 projector |
| Q3 | 深模块只返回 output + 已记录 outcome | `classify_tool_output` / `tool_result` / circuit breaker 留 core（in-process 与隔离共用） |
| Q4 | 同批合并结果信封 | 消除逐字段映射；`platform` 从我方结果去掉；`result_rejected` 成 executor 真实决策依据 |
| Q5 | 同批清测试旋钮 | monkeypatch `settings.get_bool/get_str` + `sys.platform` 替 `enabled=/allowlist=`（platform 实操受阻则保留该单参数） |
| Q6 | 命名 `IsolatedToolExecutor` | `GLOSSARY.md` 已新增词条 |
| Q7 | 单一 `run()` interface | `run(tc, tool, registry, ...)` 包决策+执行+outcome+事件；core 只调一次 |
| Q8 | 保留两层结果类型 | `IsolationResult`（seam 传输）与 `IsolatedOutcome`（executor 语义）分层，非 alias；一次构造、非逐字抄 |
| Q9 | 不改文件名 | `_isolated_exec.py` 保持，类名体现职责，测试 import 面不变 |
| Q10 | monkeypatch 系统状态替旋钮 | 删 `enabled=/allowlist=`，`platform` 视实操决定 |
| Q11 | EventType 常量为单一事实源 | 深模块引用常量，`is_known_event_type` 自动覆盖 |
| Q12 | 两提交 | ① 纯内部重构 ② core 接线改单次调用 + 旋钮删除；每提交独立 gate |

**深模块 interface（Q7）**：

```
IsolatedToolExecutor.run(
    tc, tool, registry, *,
    tool_args: dict, tool_ctx: Any,
    remaining_budget_s: float | None,
    approval_decider: Callable,
) -> IsolatedOutcome
```

内部顺序：`should_isolate` → 规则闸 → `run/arun_isolated` → E5 outcome 记录
（declared 取 `registry.timeout_capability`）→ 终态重注入 → 两类事件构造 →
返回 outcome。core 只保留：一次调用、output 交回共用后续路径。

**事件契约（Q2/Q11）**：`EventType.ISOLATION_SHADOW_DECISION = "isolation.shadow_decision"`
与 `EventType.ISOLATION_TERMINAL = "isolation.terminal"`（入注册表）；深模块用
`{"type": ..., "kind": ...}` 统一形状（具体 payload 字段在深模块内定义、测试
从公开结果断言）。

## 3. 分批与验收

| 提交 | 内容 | 验收 |
|---|---|---|
| ① 内部重构 | GLOSSARY 词条落定；EventType 两常量 + 注册表；结果类型一次构造（去死字段/`result_rejected` 派生）；测试旋钮替为系统状态；行为级测试（公开结果）替换私有管线断言 | 全平台隔离契约 + decision/wiring 测试绿；`is_known_event_type` 两新类型 True；层方向/dangling 0；数字同步 |
| ② core 接线 | `_execute_tool_call_core` 隔离分支收为 `executor.run(...)` 一次调用；删 `_emit_isolation_outcome`；删 `async_mode` 死参数；108+ 开关关回归锚 | 开关关时 loop/E5 面 118 passed / 2 xfailed 逐字不变；wiring 行为级用例绿；全量 pytest + batch_gate；数字同步 |

**回滚**：两提交独立可回滚；回退①→前态，回退②→恢复当前 B2.4 行为。全程
不改 seam 协议、生产开关默认关 / allowlist 空不变。

## 4. 风险与回滚

- 生产开关关时零行为变化是硬验收——commit ② 必须保持 118 passed 回归锚。
- 事件进注册表后 `is_known_event_type` 覆盖新增类型；观测事件不登记 projector
  不会造成 message_part 误投影（event_v2.py forward-compatible 纪律）。
- 删测试旋钮后 POSIX CI 测 win32 分支依赖 monkeypatch `sys.platform`——若影响
  面过大，按 Q10 后备保留 `platform=` 单参数（决策已授权）。
- 结果类型合并触碰测试 import 面（`IsolatedOutcome` 保留名字，字段稳定）。

## 5. 明确不做

① 改 `isolation.py` / worker 协议；② 打开生产开关或 allowlist；③ B2.5 试点；
④ 改 E5 终态语义；⑤ 收候选 4（transient/resident 三入口）、6（RSI 宿主）、
7（goal 口径）——各自独立候选，另批裁决；⑥ 改文件名。

## 6. 实施前确认

本方案来自 grilling 12 项共识（Round 1 全 A + Round 2 全 A）。实施前请维护者
确认：① 方案与共识一致；② 授权进入两提交实施。确认后按 R-007 逐提交验证。

## 7. 执行记录

### 提交① · 内部重构（2026-11-20 · 已实施）

- `event_v2.py`：EventType 增 `ISOLATION_SHADOW_DECISION` /
  `ISOLATION_TERMINAL`（入 `_ALL_EVENT_TYPES`，`is_known_event_type` 覆盖）；
  观测事件不登记 projector。
- `_isolated_exec.py`：`shadow_event`/新 `terminal_event` 用 EventType 常量、
  统一 `type` 键 + 形状；`IsolatedOutcome.from_seam_result` 一次构造（去
  platform 死字段，result_rejected 保留为 executor 决策依据）；删
  `should_isolate(enabled=/allowlist=)` 测试旋钮——改从 settings 读（测试
  用 monkeypatch 系统状态），仅保留 `platform=`（Q10 后备：POSIX CI 测
  win32 分支）。
- 测试：`test_isolated_exec_decision.py` 用 `isolation_on` fixture（env 注入）
  替旋钮；两类事件用 `is_known_event_type` 断言注册表覆盖。

验证：隔离面 105 passed（decision/wiring/context/platform/channel/pilot/
settings）；数字同步 462 文件 / 96,647 行（L3 +25）。**本提交不改 core 行为**。

### 提交② · core 接线收单次调用（2026-11-20 · 已实施）

- `IsolatedToolExecutor`（`_isolated_exec.py`）新增：`_prepare`（决策+规则闸）、
  `_settle`（E5 记录+finalize 重注入+终态事件）、`run`/`arun`（sync/async
  各一次调用）。接口单一 `run/arun(...)`，core 不再分段编排。
- `loop/tools.py`：隔离分支 40+ 行内联 → `executor.run/arun` 一次调用（按
  async_mode 选方法，core 只选方法不做编排）；删 `_emit_isolation_outcome`
  / `_tool_args` 两 helper（含死参数 `async_mode`）；4a shadow 事件改由
  executor 内部发（不再单独调用）；`_deadline` 提到分支前统一取值。
- 终态语义保真：`_settle` 保留 finalize 重注入（`_check_attempt_timeout
  (finalize=True)`）+ `isolation.terminal` 事件，与既有 E5 同构。
- 测试：`test_isolated_exec_wiring.py` 的 E5 重注入用例改测
  `IsolatedToolExecutor._settle`（新 seam）；新增 `TestExecutorRunInterface`
  两例（开关关返回 None、隔离全流程）。

验证：wiring+decision 38 passed；开关关回归锚 130 passed / 2 xfailed
（loop/E5/工具面逐字不变）；数字同步 462 文件 / 96,684 行（L3 +37）。

**两提交全部完成**——候选 1（事件收口）+ 候选 2（编排下沉）+ 顺带
候选 3（结果一次构造）、5（测试旋钮）全部落地。候选 4/6/7 独立批未动。
