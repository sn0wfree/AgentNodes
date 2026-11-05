# 既有测试缺陷台账（2026-11-04 发现，W10 期间）

> 差分验证方法：`git stash` → 清 `__pycache__` → 跑同一命令 → 对比失败集逐字一致 →
> 确认与本次改动无关。以下 7 个在 HEAD (`4cb5be49`) 上已经失败。

## 类别 A：测试 patch 了错误的模块（3 个）

| 测试 | 症状 | 根因 |
|---|---|---|
| `test_compact_error_propagation.py::TestPersistPropagatesErrors::test_persist_message_failure_logs_traceback` | `mock_logger.exception.assert_called()` 失败 | 测试 patch `agentnodes.kernel.agent.loop.logger`，但 `logger.exception` 实际由 `loop_compaction.py` 的 logger 发出。应 patch `loop_compaction.logger` |
| `test_compact_error_propagation.py::TestMaybeCompactRollsBack::test_rolls_back_when_compact_raises` | `AttributeError: loop does not have attribute 'compact_messages'` | `compact_messages` 从未在 `loop.py` 中定义（它在 `compact.py`，由 `loop_compaction.py` 调用）。应 patch `loop_compaction.compact_messages` 或 `compact.compact_messages` |
| `test_regression_2026_08_05.py::TestAmaybeCompactRuntime::test_compact_messages_invoked_via_to_thread` | 同上 | 同上 |
| `test_regression_2026_08_05.py::TestAmaybeCompactRuntime::test_llm_client_called_directly_not_via_adapter` | 同上 | 同上 |

> 共 4 个测试命中这一类（上表列 4 行）。

## 类别 B：Windows 文件锁（3 个）

| 测试 | 症状 |
|---|---|
| `test_compact_opencode_retention.py::TestProjectorKeepsMessages::test_flush_preserves_original_messages` | `tearDown` 里 `db_path.unlink()` 抛 `PermissionError: [WinError 32]` |
| `test_compact_opencode_retention.py::TestHistoryBuilderHidesCovered::test_history_hides_covered_messages` | 同上 |
| `test_compact_opencode_retention.py::TestHistoryBuilderHidesCovered::test_history_keeps_only_recent_compaction` | 同上 |

**根因**：SQLite 连接未在 `tearDown` 前关闭；Windows 不允许删除被打开的句柄。
Linux/macOS 能通过，属平台差异暴露的测试卫生问题。
**修法**：`tearDown` 先 `conn.close()`（或用 `contextlib.closing`），再 `unlink`。

## 处理决定

用户已明确本轮范围是结构三项（断环 / 契约下沉 / backtest_engine 接线）。
以上 7 个属**测试自身的缺陷**，不影响生产行为，记入待办，不在本轮修改。
（其中类别 A 的 4 个若修好，可让 compaction 的日志与 patch 行为真正被断言到，
属有价值的后续项。）

## 计数补充

`tests/attic/test_b6_l4_compaction.py:228` 仍 import `agentnodes.core.agent.loop`
——`core/` 已删除，该文件在 `attic/`（不收集），属历史残留，无需处理。
