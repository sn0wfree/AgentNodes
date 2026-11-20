# 消费者注入口归一（第二轮候选 7）R-014 方案

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **前置**：RsiHost 已收口两生产宿主（候选 6）；第二轮复审候选 7。
> **范围**：删 `make_goal_settled_consumer(run_cycle=...)` 第二注入口——测试
> 改 monkeypatch build_rsi_host，生产分支获行为覆盖，删源码文本锚。

## 0. 目标与非目标

**目标**：消费者只有一个注入 seam（RsiHost）——消除绕过生产 host 的
run_cycle 第二入口，让 6 个消费者测试真实经过已绑定 host 的路径。

**非目标**：① 不改消费者触发逻辑（should_trigger / emit / 异常吞噬）；
② 不改 RsiHost interface；③ 不改其他宿主（CLI 已用 build_rsi_host）。

## 1. 现状证据

- `goal_settled_consumer.py:116,131-135`：`run_cycle` 参数直接绕过
  `build_rsi_host()`；生产分支（build_rsi_host().run_cycle）零行为覆盖。
- `test_goal_settled_consumer.py:121,135,149,163,179,191` 6 处全走 run_cycle 注入。
- `test_rsi_host.py:59-69` 靠源码字符串 `"build_rsi_host" in text` 做架构锚——
  两个 seam 靠文本断言缝合（"interface is the test surface" 违例）。

## 2. 设计（grilling 全 A）

| # | 决策 | 落位 |
|---|---|---|
| Q1 | monkeypatch build_rsi_host | 测试 fixture 构造 `RsiHost(fake_run_once, halt, builder)`，patch `goal_settled_consumer.build_rsi_host`（或它 import 的 rsi_host.build_rsi_host）返回它；消费者测试行为不变 |
| Q2 | 删源码文本锚 | 删 test_rsi_host.py:59-69 的源码断言——生产分支已由 Q1 行为覆盖 |
| Q3 | 替身不变 | fake_run_once 返回 _Outcome（ran/ok/reason/detail），RsiHost.run_cycle 透传 |

**实现要点**：`make_goal_settled_consumer` 删 `run_cycle` 参数与 `if run_cycle is not None` 分支，`_run` 直接 `build_rsi_host().run_cycle(session_id=session_id)`。测试里的 6 处 `run_cycle=` 改为 fixture 注入 host。

**测试 fixture**：模块级 `rsi_host_stub` fixture——`RsiHost(fake_run_once, lambda:(False,""), lambda *a: object())`，monkeypatch 到消费者模块的 `build_rsi_host`；测试仍断言 calls/events/异常吞噬。

## 3. 分批与验收（单提交）

| 验收项 | 落点 |
|---|---|
| 第二注入口删除 | run_cycle 参数与分支消失 |
| 生产分支覆盖 | 6 消费者用例经 host 路径（行为断言） |
| 文本锚删除 | test_rsi_host.py:59-69 删 |
| 行为零变化 | 触发/不触发/异常吞噬用例全绿；全量 + gate |
| 数字同步 | R-007 |

**回滚**：单提交回退恢复 run_cycle 参数与文本锚，零生产行为变化。

## 4. 风险与回滚

- monkeypatch 目标：`goal_settled_consumer` 在 `_run` 内 lazy import
  `build_rsi_host`，测试需 patch `rsi_host.build_rsi_host`（模块对象）——
  lazy import 拿到的是模块属性，patch 生效。
- 替身构造：RsiHost 三参（run_once/halt_check/loop_builder），测试 fake
  直接给 lambda，不经真实 halt/wiring。
- 删文本锚后若担心"第二注入口回归"，用行为测试（本方案 Q1 已覆盖）而非
  源码字符串。

## 5. 明确不做

① 改消费者触发逻辑；② 改 RsiHost interface；③ 改 CLI 宿主；④ 碰其他候选。

## 6. 实施前确认

本方案来自 grilling 共识（全 A）。确认后按 R-007 单提交实施。
