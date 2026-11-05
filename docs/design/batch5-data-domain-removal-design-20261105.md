# 批次 5 设计：移除数据域

> 状态：**设计（待实施）** · 2026-11-05
> 上游：`docs/design/subtraction-roadmap-v2-20261105.md` 批次 5
> 起点：`00548ccd`（批次 4 完成，L2 10,294 行 / 40 文件）

---

## 0. 结论先行

| 问题 | 答案 |
|---|---|
| 删多少 | **22 文件 / 6,822 行** |
| L3 / L4 依赖 | **都是 0 条** |
| L1 依赖 | 17 条（`cli` 10 · `app.py` 3 · `mcp/server.py` 4 · `session/loop_events.py` 1） |
| L2 依赖 | 12 条（全在 `capabilities.py`） |
| 删几个 capability kind | **12 个**（AST 实测，见 §5） |
| **不删** `duckdb_store` 的实现 + `data_store_registry` | 见 §2 —— 它们是 L4 DataStore 端口的注册表 |
| **不删** `workspace_setup` | 见 §3 —— 通用脚手架，业务密度 1.8% |
| 需要安全点吗 | **不需要**（无通用件混在里面） |

> **更正记录**：本节初稿写「删 5 个 kind」，那是只看了 `datasource.*` + `data.*`。
> AST 全量核算后是 **12 个**（`db.*` 4 个与 `factor.*` 3 个的实现也在批次 5 范围）。

---

## 1. 删除范围

| 对象 | 文件 | 行数 | 业务密度 | 内容 |
|---|---:|---:|---:|---|
| `apps/data_source/` | 13 | 2,141 | 9.4% | 8 个行情 loader（tushare / akshare / yfinance / fred / eastmoney …） |
| `apps/compute_factor.py` | 1 | 1,388 | 7.1% | 因子算子库 |
| `apps/db.py` | 1 | 1,273 | 23.3% | DuckDB OHLCV 表 + CRUD |
| `apps/tools/` | 4 | 822 | 8.5% | 数据清洗 / 变换 |
| `apps/data_import.py` | 1 | 496 | 28.2% | 数据导入编排 |
| `apps/data_readiness.py` | 1 | 426 | 13.1% | 数据就绪检查 |
| `apps/preflight.py` | 1 | 276 | 12.3% | 环境检查（依赖 data_source + db） |
| **合计** | **22** | **6,822** | — | |

> 路线图预估 22 文件 6,953 行；实测 6,822 行（`tools/__init__.py` 9 行、
> `preflight.py` 276 行等细节略有出入）。另外把 `duckdb_store.py`（407）
> 和 `data_store_registry.py`（73）从删除清单里拿掉了 —— 见 §2。

---

## 2. 不删：`duckdb_store.py` + `data_store_registry.py`（框架接缝）

**它们不是业务，是 L4 `DataStore` Protocol 的实现与注册表。**

证据（`foundation/storage/data_store.py` 的模块 docstring）：

```
P0-2 separates the "data layer" into:
- DataStore Protocol (this file) — duck-typed facade every consumer targets.
- DuckDBDataStore (duckdb_store.py) — wraps the existing db.py functions as methods
- DataStoreRegistry (data_store_registry.py) — runtime lookup mirroring the LLM
  provider pattern
```

而 `app.py:54-60` 明确写了它的用途：

```python
# W10-3 (2026-11-04): fill the L4 DataStore slot. AgentLoop reads the
# slot instead of importing apps.data_store_registry (that would be a
# L3 → L2 upward import). Money path: without this, ToolContext.data_store
# is None and DB-backed tools fail at call time.
from strategy_research.apps.data_store_registry import get_store
from strategy_research.foundation.storage import register_default_data_store
register_default_data_store(get_store())
```

**裁定**：

- `data_store_registry.py` **保留** —— 它是 DI 注册表，`register(name, cls)`
  允许未来插入任意 provider（in-memory / SQLite）。零业务语义。
- `duckdb_store.py` **保留** —— 它是 `DataStore` Protocol 的 DuckDB 实现。
  但它的方法体调 `apps/db.py` 的函数，所以**必须同步改造**（见 §4）。
- `foundation/storage/data_store.py`（Protocol 本身）不动。

---

## 3. 不删：`workspace_setup.py`（通用脚手架）

168 行，业务词密度 **1.8%**（全场第二低）。它做的事：

```
把 package 的 templates/ 目录镜像到 workspace/templates/，
已存在的文件不覆盖（保护用户自定义），.prompts/ 排除（那些从包内加载）。
幂等：每次 server 启动都可以调用。
```

零业务语义 —— `_TEMPLATES_DIR` 指向 `strategy_research/templates/`，
而 templates 的去研究化是批次 7。届时 `workspace_setup` 跟着一起调整
（只镜像通用模板）。

**本批次不动。**

---

## 4. `duckdb_store.py` 的改造（关键）

它现在是 `apps/db.py` 的薄包装：

```python
class DuckDBDataStore:
    def init(self, workspace): return db.init_db(workspace)
    def get_connection(self, workspace, *, read_only=False): return db.get_conn(...)
    def save_ohlcv(self, workspace, data_map, *, strategy_name="default"): return db.save_ohlcv_to_db(...)
    def load_price_data(self, workspace, strategy_name, ...): return db.load_price_data(...)
    ...
```

`apps/db.py` 删掉后，这些方法没有实现了。

**裁定：把 `DuckDBDataStore` 的**实现**一起删，只留 Protocol。**

理由：`DataStore` Protocol 是「数据持久化的鸭子类型门面」，它定义的是
**能力契约**，不是实现。删掉业务实现后：

- Protocol 保留（`foundation/storage/data_store.py`）—— 未来任何 provider 都能实现它
- `data_store_registry.py` 保留，但**注册表初始为空**
- `get_store()` 在无注册 provider 时抛明确的 `KeyError`（现有行为，消息已含 available 列表）
- `app.py` 的 `register_default_data_store(get_store())` 改为 try/except：
  无 provider 时留空槽，`ToolContext.data_store` 为 None（现有降级路径）

这样框架仍有「可插拔数据存储」的能力，只是**不带任何具体实现**。

---

## 5. capability kind：删 5 个

| kind | 工厂 | 实现位置 | 删？ |
|---|---|---|---|
| `datasource.registry` | `_data_source_registry` | `apps.data_source.registry` | **删** |
| `datasource.base` | `_data_source_base` | `apps.data_source.base` | **删** |
| `datasource.detect_market` | `_detect_market` | `apps.data_source.utils` | **删** |
| `data.readiness` | `_check_data_readiness` | `apps.data_readiness` | **删** |
| `data.clean` | `_data_clean` | `apps.tools.data_clean` | **删** |
| `db.get_connection` | `_db_get_connection` | `apps.db` | **删** |
| `db.init_db` | `_db_init_db` | `apps.db` | **删** |
| `db.save_ohlcv_to_db` | `_db_save_ohlcv` | `apps.db` | **删** |
| `db.module` | `_db_module` | `apps.db` | **删** |
| `factor.compute` | `_compute_factor` | `apps.compute_factor` | **删** |
| `factor.compute_error` | `_factor_compute_error` | `apps.compute_factor` | **删** |
| `factor.long_to_wide` | `_long_to_wide` | `apps.tools.data_transforms` | **删** |

**实际删 12 个 kind，不是 5 个。** `db.*`（4）与 `factor.*`（3）的实现
同样住在批次 5 的删除范围内（`apps/db.py` / `apps/compute_factor.py` /
`apps/tools/data_transforms.py`）。

**能力注册 22 → 10 kind**（只剩 `evolution.*` 10 个）。

---

## 6. 删掉 12 个 kind 后，谁还会 resolve 它们

| 位置 | kind | 处置 |
|---|---|---|
| `kernel/agent/builtin_tools/data_tools.py` | `datasource.base` / `datasource.registry` / `datasource.detect_market` / `data.readiness` / `config.runner`（已降级） | 见 §7 |
| `kernel/agent/builtin_tools/data_clean_tools.py` | `data.clean` | 见 §7 |
| `kernel/agent/builtin_tools/factor_tools.py` | `db.get_connection` | 见 §7 |
| `kernel/agent/builtin_tools/backtest_analysis_tools.py` | `db.get_connection` | 见 §7 |
| `kernel/agent/builtin_tools/backtest_tools.py` | 无 resolve（直调 `resolve("backtest.script")` 已在批次 4 删） | 工具类本身待批次 6 删 |

---

## 7. kernel 侧工具：本批次只做「不注册」，不删文件

`builtin_tools/` 里的 5 个业务工具（3,074 行）按路线图归**批次 6**。
但批次 5 删掉 kind 后，它们会变成「注册了但调用抛 CapabilityNotFound」。

**本批次处置**：从 `build_default_registry()` 里**摘掉注册**，但保留文件。

| 工具类 | 依赖的 kind | 摘掉注册？ |
|---|---|---|
| `DataCleanTool` | `data.clean` / `db.get_connection` / `db.save_ohlcv_to_db` | **是** |
| `CheckDataTool`（在 data_tools.py） | `datasource.*` / `data.readiness` | **是** |
| `ListDataSourcesTool` / `SearchSymbolTool` | `datasource.*` | **是** |
| `ComputeFactorTool` / `FactorAnalysisTool` / `PatternRecognitionTool` | `factor.compute` / `db.get_connection` | **是** |
| `StrategyCompare` / `DrawdownAnalysis` / `BenchmarkComparison` | `db.get_connection` | **是** |
| `RunBacktestTool` | `backtest.script`（批次 4 已删） | **是**（批次 4 遗留） |

这样 agent 的 tool 列表里不再出现「调用必然失败」的工具 —— 比留着让
LLM 撞墙好。文件保留到批次 6 一起删（那时连代码一起清）。

---

## 8. L1 出口处置

| 文件 | 条数 | 处置 |
|---|---:|---|
| `cli/__init__.py` | 10 | 删 `import` / `validate`（已删）/ `preflight` 三个子命令；`cmd_import` 的 3 个 helper（`_import_local_file` / `_import_api_source` / `_import_fred`） |
| `api/app.py` | 3 | `data_store_registry` 改 try/except（§4）· 删 `init_db` 调用 · `workspace_setup` 保留 |
| `mcp/server.py` | 4 | 删 `ListDataSourcesTool` / `SearchSymbolTool` 注册 |
| `session/loop_events.py` | 1 | 需看具体用法 |

---

## 9. 测试处置

预判（实施时以门禁实测为准）：

| 类别 | 数量 | 处置 |
|---|---:|---|
| 已归档（批次 1/2） | ~20 | `test_db_*` / `test_data_import_*` / `test_data_source_*` / `test_compute_factor_*` / `test_data_transforms` / `test_data_clean` / `test_duckdb_store_*` / `test_preflight` 等 |
| 在 `tests/` 的 | ~8 | `test_data_readiness.py` / `test_data_tools_unit.py` / `test_market_data_cache_flow.py` / `test_load_ohlcv.py`… |

---

## 10. 执行顺序

```
1. 本设计文档（doc first）                        ← 当前
2. 门禁先跑 —— check_dangling_imports 拿精确清单
3. 删 apps 22 文件（data_source / compute_factor / db / tools /
   data_import / data_readiness / preflight）
4. 改造 duckdb_store.py（删实现，只留 Protocol 侧）+
   data_store_registry.py（空注册表）
5. 改 capabilities.py（22 → 10 kind，删 12 个工厂）
6. 从 build_default_registry 摘掉 9 个业务工具注册
7. 删 L1 出口（cli 3 子命令 + app.py + mcp + loop_events）
8. 归档/删除测试 + 收集 0 errors
9. 全量回归（与批次 4 的 60 failed 差分）
10. sync_architecture_diagram.py --write + 版本史 v10.2
11. commit
```

---

## 11. 预期效果

| 指标 | 批次 4 后 | 批次 5 后 |
|---|---:|---:|
| L2 行数 | 10,294 | **~4,100** |
| L2 文件 | 40 | **~18** |
| 总计 | 82,163 | **~76,000** |
| 跨层边 L1→L2 | 27 | ~8 |
| 能力 kind | 22 | **10**（只剩 evolution.*） |
| 上行违规 | 2 | 2（不变） |

---

## 12. 不在本批次范围

| 项 | 归属 |
|---|---|
| `builtin_tools/` 5 个业务工具文件的删除 | 批次 6 |
| `templates/` 去研究化 | 批次 7 |
| `workspace_setup.py` 的模板范围调整 | 批次 7 |
| `evolution` 去研究化 | 批次 8 |
