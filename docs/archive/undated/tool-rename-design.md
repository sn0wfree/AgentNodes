# 工具重命名设计文档

## 目标

将基础工具名称对齐 opencode 风格：简洁、一致、1-12 字符。

## 重命名清单

| 模块 | 当前名称 | 新名称 | opencode 对应 |
|------|----------|--------|---------------|
| `file_tools.py` | `read_file` | `read` | `read` |
| `file_tools.py` | `list_files` | `list` | `list` |
| `file_tools.py` | `write_file` | `write` | `write` |
| `shell_tools.py` | `run_command` | `shell` | `bash`/`shell` |
| `subagent_tool.py` | `delegate_to_agent` | `task` | `task` |
| `todo_tools.py` | `todo_write` | `todowrite` | `todowrite` |
| `workspace_tools.py` | `load_skill` | `skill` | `skill` |
| `web_tools.py` | `web_search` | `websearch` | `websearch` |
| `web_tools.py` | `read_url` | `webfetch` | `webfetch` |

## 不改动的工具

以下工具保持原名（无 opencode 对应）：

- `git_diff`, `list_history`, `list_skills`
- `run_backtest`, `compute_factor`, `factor_analysis` 等研究工具
- `create_goal`, `add_evidence` 等目标工具
- `get_market_data`, `search_symbol` 等数据工具
- `show_chart`, `show_report` 等显示工具
- `tool_help`, `clean_data`, `options_pricing` 等杂项工具

## 影响分析

### 后端

1. **工具模块** — 7 个文件的 `name` 属性
2. **注册表** — `build_default_registry()` 中的注册调用
3. **测试** — 工具名引用（`test_all_tools.py` 等）

### 前端

1. **SSE handlers** — 工具名匹配
2. **ToolCallBlock** — 工具图标映射、摘要生成
3. **ToolCallGroup** — 分组逻辑

### 兼容性

- LLM prompt 中的工具描述会自动更新（从 OpenAI schema 生成）
- 无向后兼容需求（工具名是内部 API）

## 实施顺序

1. 修改 7 个工具模块的 `name` 属性
2. 更新 `build_default_registry()` 注册调用
3. 更新前端工具名映射
4. 更新测试
5. 运行全量测试验证
