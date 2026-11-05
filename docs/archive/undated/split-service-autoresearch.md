# 拆巨文件 — service.py / autoresearch.py

> 2026-09-29 · 维护性重构（行为不变）

## service.py（~2.2k 行）拆分

| 新模块 | 迁出内容 |
|---|---|
| `api/session/history_convert.py` | `_convert_messages_to_history` 及 compaction/tool 索引工具 |
| `api/session/part_accumulate.py` | `_accumulate_text_*` / `_build_tool_call_part` / `_accumulate_tool_result` |
| `api/session/loop_events.py` | `_LoopEventForwarder`、`_build_attempt_metrics`、`_emit_goal_event` |

`SessionService` 保留编排；原私有名 **re-export 兼容**。

## autoresearch.py 拆分

| 新模块 | 迁出内容 |
|---|---|
| `core/study/lazy_detect.py` | `detect_lazy_behavior` + per-role detectors |
| `core/study/stub_agents.py` | `_stub_*` 研究角色桩 |
| `core/study/run_summary.py` | `generate_run_summary` / save / load |

`autoresearch.py` 保留 spawn/phase 主流程 + 兼容 re-export。

## 验收

```text
pytest tests/test_session_queue.py tests/test_projector.py tests/test_study_comprehensive.py tests/test_autoresearch.py tests/test_convert_messages_to_history_order.py -q
```
