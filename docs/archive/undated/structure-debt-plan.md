# 结构债清理（W 系列）

> 2026-09-29 · 对应 issues-recheck H3/H5/M1/M2/H4  
> 状态: **W1 · W2 · W3a · W3b done** · W4/W5 待做

## W1 — 删除根 `templates/` 副本

- **证据**：全部路径解析到 `src/strategy_research/templates`（`__init__._TEMPLATES_DIR`、`parents[2]/templates`）
- **风险**：无运行时引用；根目录 37 文件为漂移副本
- **动作**：`git rm -r templates/`

## W2 — CLI 合并（后续）

`serve` / `webui` / `api` → `serve`；`run`/`evaluate`/`reproduce` → `backtest`；`validate`/`validate-run`/`accept` → `check`

## W3 — 删 HTMX + deprecated 事件桥

- `src/strategy_research/webui/routes.py` + jinja templates
- `api/session/events.py` / `event_bus_v2.py` → `_archived` 或删除

## W4 — backtest models 三合一

## W5 — loop.py 再拆

---

## 验收

```text
pytest tests/test_workspace_setup.py tests/test_cli_build_parser.py -q
```
