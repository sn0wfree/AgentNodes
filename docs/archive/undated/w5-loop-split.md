# W5 — 拆 `_execute_tool_call_core`

> 2026-09-29 · Agent loop 复杂度削减  
> 目标：C901 `_execute_tool_call_core` 20→≤15，可单测的纯函数抽出

## 问题

`AgentLoop._execute_tool_call_core` 一口吞下：缺工具/guard/注入 kwargs/重试/熔断/事件/渲染。单元测试只能黑盒。

## 方案：抽 `runtime/tool_exec.py` 纯件

| 新函数 | 职责 |
|---|---|
| `missing_tool_payload` | 无工具 → event + tool message |
| `guard_denied_payload` | guard 拒绝 |
| `build_tool_kwargs` | workspace/session/ctx 注入 |
| `classify_tool_output` | is_error / preview / full |
| `render_history_output` | A3 视图 + 回退截断 |
| `tool_result_message` | role=tool 消息 |

`_execute_tool_call_core` 只保留：查找 → 守卫 → 注入 → 重试循环 → 发事件 → 返回消息。

## 验收

```text
pytest tests/test_agent_runtime.py tests/test_tool_result_views.py tests/test_agent_tools.py -q
ruff check --select C901 src/strategy_research/core/agent/loop.py
```
