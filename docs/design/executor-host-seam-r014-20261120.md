# 执行器宿主 seam 收口（第二轮候选 3）R-014 方案

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **前置**：隔离执行器深化已验收（`c3a2a417`）；
> 第二轮复审 `architecture-review-20261120-150939.html` 候选 3。
> **范围**：执行器不再读宿主私有数据字段，改经薄适配器的能力方法；
> 两份 HostStub 合并。零行为变化。

## 0. 目标与非目标

**目标**：`IsolatedToolExecutor` 的 host seam 落在**能力**上而非宿主内部状态上——
消除 `_attempt_deadline` / `_current_iter` 私有字段穿透，finalize 重注入
语义内聚到宿主侧一个方法，两份重复 stub 合成一份。

**非目标**：① 不改 E5 终态语义（finalize 仍是 timeout ending）；② 不改
执行器 run/arun interface；③ 不改 loop 的 `_check_attempt_timeout` 本身；
④ 不加 GLOSSARY 词条（Q4-B：内部私有 adapter 不收录）。

## 1. 现状证据

- `_isolated_exec.py:325-327`：`_settle` 用 `getattr(self._host, "_check_attempt_timeout")`
  与 `getattr(self._host, "_attempt_deadline")` / `_current_iter`——执行器读宿主
  私有数据字段，seam 落在宿主内部状态。
- `tools.py:148` 传整个 loop self 给 `IsolatedToolExecutor(self)`；host 消费面
  实际 7 个成员（registry / _trace_and_emit / _record_timeout_outcome /
  _check_attempt_timeout / _attempt_deadline / _current_iter），名单只在
  docstring 和测试里。
- `test_isolated_exec_wiring.py` 两份 HostStub（:160-180 / :222-242）逐字重复。
- `core.py:988-1019` `_check_attempt_timeout` 在 deadline 到点时**抛**
  AttemptTimeoutError；执行器注释声明"不直接抛 AttemptTimeoutError"——语义张力。

## 2. 设计（grilling 决策落位）

| # | 决策 | 落位 |
|---|---|---|
| Q1 | 薄适配器放 tools.py | `ExecHost` 类 + `exec_host(loop) -> ExecHost`，收 4 能力：registry / trace_and_emit / record_timeout_outcome / finalize_timeout |
| Q2 | finalize 只做副作用不抛 | `ExecHost.finalize_timeout()` 内部 try `_check_attempt_timeout(iter, None, finalize=True)` / except AttemptTimeoutError: pass——复用既有逻辑、吞异常刻意（终态重注入只需事件副作用，AttemptTimeoutError 由外层边界处理）；测试钉住"不抛" |
| Q3 | 两份 stub 合并 | 一份 fixture 构造 ExecHost 兼容 stub（4 能力），两测试类共用 |
| Q4 | 不加 GLOSSARY | 内部私有 adapter，`ExecHost` 类名自解释 |

**改后执行器消费面**：`host.registry` / `host.trace_and_emit` /
`host.record_timeout_outcome` / `host.finalize_timeout`——4 个能力方法，
无私有数据字段读取。`_settle` 的 terminal 分支直接调
`self._host.finalize_timeout()`（无 getattr / 无 deadline 判断）。

**loop core 侧**：`IsolatedToolExecutor(exec_host(self))` 一行；`ExecHost`
是 tools.py 的私有类（模块内定义，不导出）。

## 3. 分批与验收（单提交）

| 验收项 | 落点 |
|---|---|
| 执行器不读宿主私有态 | `_settle` 无 `_attempt_deadline` / `_current_iter` getattr |
| finalize 不抛 | ExecHost.finalize_timeout 测试：deadline 已到也不抛 |
| stub 合并 | 两份 HostStub → 一份 fixture |
| E5 语义不退化 | 开关关回归 130 passed/2 xfailed；终态重注入测试绿 |
| 全量 + gate + 数字 | R-007 |

**回滚**：单提交回退恢复当前读私有字段实现，零协议变化。

## 4. 风险与回滚

- `finalize_timeout` 吞 AttemptTimeoutError 是**刻意**——与执行器原注释语义一致
  （终态重注入只产事件、不抛）；若未来需要由执行器抛，另立方案。
- 适配器只包 4 能力——若执行器未来要更多宿主能力，必须显式加进 adapter
  （seam 收紧的收益）。
- `exec_host(self)` 每次调用创建轻量适配器对象（无状态）——无性能问题。

## 5. 明确不做

① 改 `_check_attempt_timeout` 本身；② 改 E5 终态语义；③ 加 GLOSSARY 词条；
④ 改执行器 run/arun interface；⑤ 碰候选 4/5。

## 6. 实施前确认

本方案来自 grilling 共识（Q1-Q3 全推荐 + Q4-B）。确认后按 R-007 单提交实施。

## 7. 执行记录（2026-11-20 · 已实施）

- `tools.py` 新增私有 `_ExecHost` 适配器（4 能力：registry / trace_and_emit /
  record_timeout_outcome / finalize_timeout）；`finalize_timeout` 内部
  try `_check_attempt_timeout(...)` / except AttemptTimeoutError: pass——
  终态重注入只产事件副作用，不抛（Q2 钉住）。
- `loop core` 传 `IsolatedToolExecutor(_ExecHost(self))`；执行器 4 处消费
  全改能力方法，`_settle` 的 terminal 分支直接 `self._host.finalize_timeout()`，
  不再读 `_attempt_deadline` / `_current_iter` 私有字段。执行器 docstring
  同步 host seam 描述。
- 测试两份 HostStub → 一份 `exec_host_stub` fixture（4 能力、无私有字段）；
  新增 `test_exec_host_finalize_swallows_timeout_error` 钉住"不抛"。

验证：wiring 15 passed；开关关回归 70 passed（loop/E5/timeout 面逐字不变）；
数字同步 464 文件 / 96,887 行（L3 +34）。零行为变化、E5 终态语义不变。
