# Tool Set Convention

> Status: v1 (proposed) — 2026-08-05
> Author: AI assistant (under user direction)
> Scope: `src/strategy_research/`

## 1. 三类工具的划分

### 1.1 Agent Tool（LLM-callable）

- **位置**: `core/agent/builtin_tools/`
- **特征**: 继承 `BaseTool`，注册到 OpenAI function calling
- **被谁调用**: LLM agent（通过 JSON schema 工具调用）
- **示例**: `ReadFileTool`, `RunBacktestTool`, `ComputeFactorTool`
- **未来子目录**（# TODO(agent-tool-categories)）:
  - `data/` — 数据获取与变换
  - `factor/` — 因子计算与分析
  - `git/`, `meta/`, `shell/`, `goal/`

### 1.2 Project Tool Set（Python 多函数模块）

- **位置**: `core/tools/`
- **特征**: 一个文件 = 一个主题 + 多个相关函数
- **被谁调用**: 项目代码 + 1.1 工具的内部实现
- **判断标准**: 模块内是否有 ≥3 个相关函数围绕一个主题？
  - 是 → tool set
  - 否 → utils（见 1.3）
- **现有实例**:
  - `core/tools/data_clean.py` — 数据清洗工具集
  - `core/tools/data_transforms.py` — 数据形状变换工具集（**#2**）
- **未来实例**（示例）: `core/tools/factor_expr_helpers.py`

### 1.3 Common Util（Python 单函数模块）

- **位置**: `core/utils/`
- **特征**: 一个文件 = 1-2 个 helper，主题不形成"工具集"
- **被谁调用**: 项目代码
- **示例**:
  - `token_utils.py` — `estimate_tokens(text)`
  - `market_detection.py` — `detect_market(code)`
  - `backtest_config.py` — `CostConfig` dataclass
- **多函数但未评估的候选**（# TODO(toolsets)）:
  - `backtest_utils.py` (7+ 函数) — 评估是否升级为 tool set
  - `io_utils.py`, `metrics.py`, `risk_parity.py`, `ic_utils.py` 等

## 2. Tool Set 设计规范（适用于所有 `core/tools/*.py`）

### 2.1 文件模板

```python
"""<主题> tool set #N（after <prev_set>）。

# TODO(toolsets): <未来扩展点>。

Conventions:
  - LONG_OHLCV   = DataFrame[date, asset, open, high, low, close, volume]
  - WIDE_OHLCV   = dict[asset, DataFrame(date, [ohlcv])]
  - WIDE_CLOSE   = DataFrame(date, [asset_codes])
  - WIDE_FACTOR  = same as WIDE_CLOSE
"""
from __future__ import annotations
import pandas as pd
# ...
```

### 2.2 命名约定

- 函数名: `<input_format>_to_<output_format>_<detail>`
  - 例: `long_to_wide_close`, `wide_factor_to_long`
- 单一职责: 一个函数只做一种变换
- 强类型注解: `pd.DataFrame`, `dict[str, pd.DataFrame]`, `pd.Series`

### 2.3 失败处理

- **fail-fast**: 输入格式不匹配时，抛 `ValueError`/`TypeError` 带 actionable message
- **不静默 fallback**: 不返回 `None` / 空 DataFrame / `0.0`
- 例:
  ```python
  if not {"date", "asset"}.issubset(df.columns):
      raise ValueError(
          f"long format expects columns [date, asset, ...], got {list(df.columns)}"
      )
  ```

## 3. data_transforms 工具集详细设计

### 3.1 7 个 helper

| # | 函数 | 输入 | 输出 | 用途 |
|---|---|---|---|---|
| 1 | `long_to_single_asset_wide` | long DataFrame + asset 名 | wide(T, 5) | 单 asset 给 DSL |
| 2 | `long_to_wide_close` | long DataFrame | wide(T, N) | close-only 面板 |
| 3 | `long_to_wide_ohlcv_per_asset` | long DataFrame | dict{asset: wide(T, 5)} | 多 asset 给 DSL 循环 |
| 4 | `wide_close_to_long` | wide(T, N) | long[date, asset, close] | 持久化 |
| 5 | `wide_factor_to_long` | wide factor + name | long[date, asset, factor_name] | 因子存库 |
| 6 | `wide_to_long_ohlcv` | dict{asset: wide(T, 5)} | long[date, asset, ohlcv] | 多列持久化 |
| 7 | `is_wide_close_format` | DataFrame | bool | 自检（wide(T,N) 列名像 asset code） |

### 3.2 失败行为（fail-fast）

| 场景 | 行为 |
|---|---|
| `long_to_single_asset_wide` 给的 asset 不存在 | `ValueError("asset X not found in data; available: [...]")` |
| `long_to_*` 输入不是 long（缺 date/asset 列） | `ValueError("long format expects columns [date, asset, ...]")` |
| `wide_close_to_long` 输入已经是 long | 抛 `ValueError`（不静默跳过） |
| `is_wide_close_format` 永远不抛 | 返回 bool |

### 3.3 与 ComputeFactorTool 的契约

- **ComputeFactorTool 不再内联 4 行转换**（取消）
- 改用 `long_to_single_asset_wide(prices_df, asset=asset, value_cols="ohlcv")`
- 行为完全等价，但代码统一
- 8 处工具代码统一调用同一 helper

### 3.4 与 FactorStrategy.compute_weights 的契约（核心修复）

- **fail-fast**: 检测到 wide(T,N) close-only 而非 wide(T,5) ohlcv 时，抛 `RuntimeError("FactorStrategy needs long ohlcv panel; got multi-asset close-only wide format. Call long_to_wide_ohlcv_per_asset first.")`
- 修复路径: `compute_weights` 内**先**调 `long_to_wide_ohlcv_per_asset(self._load_long_ohlcv())`，**再**逐 asset 调 `compute_factor`
- 旧行为（错误）: 一次性传 wide(T,N) close-only 给 DSL → 全部因子失败 → metrics=0/NaN
- 新行为（正确）: 逐 asset 算，组合回 wide(T,N) factor → 真实 ranking → 真实 metrics

## 4. 迁移计划

### 4.1 已迁移

- `core/data_clean.py` → `core/tools/data_clean.py`（2026-08-05）

### 4.2 未来评估（# TODO(toolsets)）

- `core/utils/backtest_utils.py`（7+ 函数）— 评估是否升级为 `core/tools/backtest_utils.py`
- `core/utils/io_utils.py`, `metrics.py`, `risk_parity.py`, `ic_utils.py`, `strategy_engine.py` — 逐一审查

## 5. 不放入 tool set 的内容

- **LLM 工具**: 走 1.1，agent 工具子目录拆分（# TODO(agent-tool-categories)）
- **数据源适配器**: 已在 `core/data_source/` 独立子包，不动
- **数据库 schema**: 在 `core/db.py` 集中管理，不动

## 6. 相关决策

- D-2026-08-05-1: 工具集扁平文件，**不**用 registry 装饰器（避免过度设计）
- D-2026-08-05-2: tool set 内的函数**不**注册到 LLM（保持纯 Python 内部复用）
- D-2026-08-05-3: 工具集是"按主题聚合的纯函数模块"，通过 `import` 发现
- D-2026-08-05-4: fail-fast 是默认行为，不静默 fallback
- D-2026-08-05-5: 现有 `data.duckdb` 7500 行 wide 数据**保留**（修后第一次跑 backtest 不破坏用户数据）

## 7. 未来扩展

- # TODO(toolsets): 把高频 tool set 函数暴露为 LLM 工具（`LongToWideTool`）
- # TODO(toolsets): `pyproject.toml [project.entry-points]` 让第三方注册工具集
- # TODO(toolsets): `core/agent/builtin_tools/data/` 子目录落地（拆 `__init__.py` 22+ 工具）
- # TODO(agent-tool-categories): 拆分 1.1 子目录
- # TODO(l4-compact-no-user): 修 compaction L4 "no user role" 警告
- # TODO(data-migration): 评估现有 `data.duckdb` 7500 行 wide 数据迁移策略
