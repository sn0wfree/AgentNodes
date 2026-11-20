# `IsolationChannel.read()` 返回契约收口 R-014 方案（第三轮复审候选 I2）

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **来源**：第三轮架构复审候选 I2（`C:\Users\1\AppData\Local\Temp\architecture-review-20261120-163911.html`）。
> **前置**：B2.2/B2.3/B2.4 隔离通道已实施验收。
> **范围**：让「读超时 vs EOF」的区分成为 `read()` 的返回契约，消除调用方对学生私有字段的穿透。

## 0. 目标与非目标

**目标**：
- `IsolationChannel.read()` 返回显式结构 `ReadResult(kind=data|timeout|eof, data=...)`；
- 生产调用方 `_execute_on_channel` 不再读 `channel._eof`，改按返回的 kind 分支；
- 同批加 `channel.stderr_tail()`，`_execute_on_channel` 不再读 `channel._handle["proc"].stderr`；
- 测试从私有字段断言（`_EOF` / `_queue` / `_eof`）改为返回契约断言。

**非目标**：
- 不改毒化 / EOF / deadline 语义（3c D1/D2 冻结：`unknown` 时 timed_out → kill+confirm → 毒化；EOF 不毒化）；
- 不改 `drain_until`（零调用者但属冻结删除批——保留原样，只适配新 read 契约）；
- 不改任务行协议 / ctx codec / E5 / 准入；
- 不碰冻结删除批；worker 脚本不动。

## 1. 现状证据（已回读源码核对）

### 1.1 读契约与私有穿透
- `read()`（`isolation.py:211-223`）：`queue.Empty` 与 `obj is _EOF` 都返回 `None`，靠副作用 `self._eof = True`（`:221`）区分。`_EOF` 定义在 `:101`。
- 生产调用方 `_execute_on_channel`（`:352-362`）：`obj is None` 后读 `channel._eof`（`:355`）决定 EOF-break vs `timed_out=True`。**timeout/EOF 区分是生产真实需求**，不是测试捏造。
- 第二处穿透：`:418` `channel._handle["proc"].stderr` 读 stderr tail（诊断，best-effort）。
- 私有穿透表（外部读取 IsolationChannel 私有字段）：`_eof`（:355 生产 + 测试 :79/:84）、`_handle`（:418 生产）、`_queue`（测试写入 :82/:170/:174）、`_EOF` 模块常量（测试 import :22）。

### 1.2 语义链（必须保持）
`_execute_on_channel` 的 `timed_out` → `IsolationWorker.call`（`:300-308`）：`unknown` 且 timed_out → `_confirm_termination`（kill+逐 pid 确认）→ 毒化关闭；EOF 无结果 → timed_out=False → 不杀、但仍毒化。`b23:134,170` 明确「毒化只属 deadline/EOF，解码失败不毒化」。

### 1.3 测试锚
- `test_isolation_platform_matrix.py:67-84` `test_read_timeout_is_not_eof`：import `_EOF`（:22）、`channel._queue.put(_EOF)`（:82）、断言 `channel._eof` False/True（:79/:84）。
- `test_isolation_platform_matrix.py:129-189` `test_stale_row_discarded_and_counted`：monkeypatch `channel.read`（:162/:178/:180），依赖 read 返回原始事件 dict；需同步适配。
- `isolation_channel.py / pilot / adapter / decider_crash / context / admission` 等测试不碰 read/_eof/_queue——零改动。

## 2. 设计（grilling 全 A：Q13-Q16）

| # | 决策 | 落位 |
|---|---|---|
| Q13 | 新增不可变 `ReadResult`（dataclass）：`kind: str`（`"data"` / `"timeout"` / `"eof"`）+ `data: dict | None`；`read()` 返回它，`_eof` 保留为内部实现细节 | `isolation.py` |
| Q13′ | `_execute_on_channel` 改按 `result.kind` 分支：data → 用 `result.data` 走原 id 过滤/结果处理；timeout → 原 deadline 判定；eof → break | `isolation.py:352-362` |
| Q14 | 新增 `IsolationChannel.stderr_tail(limit=500) -> str`，内部读 stderr，best-effort 保留；`_execute_on_channel` 改调它 | `isolation.py:417-423` |
| Q15 | `drain_until` 保留（零调用者、冻结删除批），只把内部 read 消费改为按 kind | `isolation.py:225-238` |
| Q16 | `test_read_timeout_is_not_eof` 改断言返回 kind（不再 import `_EOF` / 写 `_queue` / 读 `_eof`）；`test_stale_row_discarded_and_counted` 同步适配；毒化/EOF/deadline 语义逐字不变 | 测试 |

**实现要点**：
- `ReadResult` 用 `@dataclass(frozen=True)`（与 `IsolationResult` 同款风格），`kind` 用字面量字符串（与 `ACTUAL_STATES` 词表风格一致，不引 Enum）。
- `read()`：`queue.Empty` → `ReadResult("timeout", None)`；`obj is _EOF` → 置 `_eof=True`、`ReadResult("eof", None)`；否则 `ReadResult("data", obj)`。
- 测试用工厂注入 EOF：直接构造 `ReadResult("eof", None)` 或保留一个内部 helper；不再需要外部写 `_queue` / import `_EOF`。
- `stderr_tail()`：把 `:417-423` 的 try/except 逻辑原样搬入 channel 方法，`_execute_on_channel` 一行调用。

## 3. 分批与验收（单提交）

| 验收项 | 落点 |
|---|---|
| read() 返回 ReadResult（三 kind） | isolation.py |
| 生产调用方零私有字段穿透 | `_eof` / `_handle` 外部读取为 0 |
| drain_until 保留且适配 | 行为不变 |
| 测试锚改返回契约 | test_isolation_platform_matrix 两例 |
| 毒化 / EOF / deadline 语义不变 | 既有隔离测试全绿 |
| 数字同步 | R-007 |

**回滚**：单提交回退恢复；零残留。

## 4. 风险与回滚

- **语义等价性**：timeout/EOF 判定必须与旧 `_eof` 分支逐字等价——先写判别测试（超时 kind=timeout、EOF kind=eof、数据 kind=data），再改生产分支。
- **stale-row 测试**：monkeypatch 的 fake read 需返回 `ReadResult("data", original_row)`——适配时保证 id 过滤/discard 计数断言不变。
- **stderr best-effort**：`stderr_tail()` 吞异常、返回 "" 的行为逐字不变。
- **零调用者 drain_until**：改 read 消费方式但保留函数（冻结删除批纪律）。

## 5. 明确不做

① 改毒化 / EOF / deadline / E5 语义；② 删 drain_until；③ 改任务行协议 / ctx codec / 准入；④ 改 worker 脚本；⑤ 任何删除批动作。

## 6. 实施前确认

本方案来自 grilling 全 A（Q13-Q16）。**未获批不得实施任何代码**；获批后按 R-007 单提交实施，doc first 纪律已满足（本文件）。
