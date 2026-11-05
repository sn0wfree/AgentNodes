# DuckDBDataStore 适配层对齐（A-class 续）

> 同类问题：facade 用现代签名调用 legacy `core/db.py`，kwargs 全对不上（运行时 TypeError）
> 生产调用方走 `db.py` 直呼（`backtest.py` / `data_import.py`），facade 路径目前无生产调用——**潜伏 bug**

## 1. 漂移表

| Facade 方法 | 实际传给 `_db` | `db.py` 真参 | 修法 |
|---|---|---|---|
| `save_factor_data` | `values=` | `factor_values` | 位置/改名 |
| `save_backtest_result` | `run_id=` `result=` | `strategy_name, run, commit_hash, …` | **dict 解包** |
| `save_weight_history` | `run_id=` `weights=` | `strategy_name, run, weights_history` | 补 `strategy_name` + DataFrame→list |
| `load_weight_history` | `run_id` | `strategy_name, run` | 补 `strategy_name` |
| `save_nav_history` | `run_id=` `nav=` | `strategy_name, run, nav` | 补 `strategy_name` |
| `load_nav_history` | `run_id` | `strategy_name, run` | 补 `strategy_name` |
| `cache_validation` | `metrics=` `ttl_seconds=` | `factor_code, ic_mean, …` | **dict 解包**（无 TTL 列，忽略） |
| `get_last_import_date` | `codes=` | `strategy_name, asset_code` | 补 `strategy_name` + 单码 |
| `update_import_meta` | `codes=` `import_date=` `row_count=` | `strategy_name, asset_code, last_date, source` | 补 `strategy_name` + 循环 codes |
| `update_data_fingerprint` | `fingerprint=` | `table_name, fingerprint, row_count` | 补 `table_name` |

## 2. 设计决策

**同 A.4：修 facade 适配，不改 `db.py`（30+ 直呼调用方）。**

- Protocol 保持不变；facade 在需要处加**可选** `strategy_name: str = ""` / `table_name: str = "ohlcv"`（runtime Protocol 只查属性，不比签名）
- dict → 位置/关键字参数用 `result.get(...)` 带默认值，与 `db.py` 默认一致
- `save_weight_history`：`DataFrame` 按 `iterrows` 收成 `list[tuple[Timestamp, dict]]`（兼容 Protocol 的 DataFrame 与 legacy list 两种输入）
- 导入元数据 `codes: list` → 对每个 code 调一次 `update_import_meta`；`get_last_import_date` 对 `codes[0]` 或 `None` 返回 `None`

## 3. 验收

- [x] pyright `duckdb_store.py` `reportCallIssue` → 0（另修 `list_backtest_results` status/limit 错位）
- [x] 单测 `test_duckdb_store_adapter.py` ×6 mock `_db.*` 断言 kwargs
- [x] 既有 protocol/factors/db_persistence 30 passed
- [x] 同批：`runner.py` AcceptanceDecision stub、`slash_goal` AuditRow 列表 + criterion_id

**落地**（2026-10-08）：facade 按 `db.py` 真参解包 dict / 补 `strategy_name`；DataFrame→legacy list；import_meta 循环 codes。
