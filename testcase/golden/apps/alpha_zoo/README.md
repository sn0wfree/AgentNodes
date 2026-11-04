# alpha_zoo 金标准快照

> 归档时间：2026-11-04（做减法批次 1）
> 原路径：`src/strategy_research/apps/alpha_zoo/` + 4 个同级模块

## 为什么归档而不是直接删

这三个东西有真实的复用价值，不是一次性代码：

1. **`alpha_zoo_yaml.py`（325 行）**：一个完整的**表达式 DSL 求值器**
   （`evaluate_node` / `_call_operator` / `compute_alpha_from_yaml`）。它读 YAML
   描述的因子表达式树、按算子求值。这套「DSL → 计算」的机制与量化无关，
   任何需要「配置化计算图」的场景都能复用。

2. **`alpha_zoo_convert.py`（1,011 行）**：**Python AST → YAML AST 的转换器**。
   `ComputeFunctionAnalyzer` 用 `ast.NodeVisitor` 把因子函数的 Python 源码
   转成声明式 YAML。这是「代码 → 配置」的通用能力，做 Agent 的代码理解时用得上。

3. **`alpha_zoo_ops.py`（238 行）**：21 个 pandas/numpy 算子（`ts_rank` /
   `decay_linear` / `signed_power` / `safe_div` / `vwap` 等）。纯数值工具，
   与因子无关，任何时序计算都能用。

而被删的 468 个因子实现文件（`alpha101/` `gtja191/` `qlib158/` 等）是**手写
的业务资产**，不是生成产物——每个文件都有中文注释和论文引用。它们只对
量化业务有意义，所以只保留代表性样本。

## 目录

```
golden/apps/alpha_zoo/
├── README.md                 本文件
├── loader.py                 511 行 · 因子加载与计算入口（AlphaLoader）
├── alpha_zoo_ops.py          238 行 · 21 个数值算子（可复用）
├── alpha_zoo_yaml.py         325 行 · YAML 表达式 DSL 求值器（可复用）
├── alpha_zoo_convert.py    1,011 行 · Python AST → YAML AST（可复用）
├── alpha_zoo_adapter.py       20 行 · 兼容转发层
├── samples/                  6 个代表性因子（各库一个）
│   ├── alpha101_alpha_001.py + .yaml   Kakushadze #1（论文公式 + 完整实现）
│   ├── gtja191_alpha_001.py            国泰君安 191 #1
│   ├── qlib158_beta10.py               Qlib 158 · beta10
│   ├── academic_carhart_mom.py         Carhart 动量
│   └── fundamental_earnings_yield.py   盈利收益率
└── yaml_defs/                408 个 YAML 因子定义（全量保留，体积小）
```

## 全量历史

完整代码在 git 历史里可取：

```bash
git show c0bd756e:src/strategy_research/apps/alpha_zoo/loader.py
git ls-tree -r c0bd756e --name-only src/strategy_research/apps/alpha_zoo/
```

## 复用示例

### 用 DSL 求值器算一个因子

```python
from testcase.golden.apps.alpha_zoo.alpha_zoo_yaml import compute_alpha_from_yaml

config = {
    "id": "demo",
    "steps": [
        {"name": "close", "expr": {"column": "close"}},
        {"name": "ret", "expr": {"op": "pct_change", "args": [{"ref": "close"}]}},
        {"name": "out", "expr": {"op": "rank", "args": [{"ref": "ret"}]}},
    ],
}
result = compute_alpha_from_yaml(config, {"close": df})
```

### 把 Python 因子函数转成 YAML

```python
from testcase.golden.apps.alpha_zoo.alpha_zoo_convert import convert_py_to_yaml
config = convert_py_to_yaml(Path("samples/alpha101_alpha_001.py"))
# -> {"id": "alpha101_001", "steps": [...]}
```

### 直接用算子

```python
from testcase.golden.apps.alpha_zoo.alpha_zoo_ops import ts_rank, decay_linear
```

## 注意

- 本目录**不被 pytest 收集**（`testpaths = ["tests"]`），也不进 ruff 门禁。
- `samples/` 里的因子文件 import 了 `..alpha_zoo_ops`，单独跑需要调整相对导入；
  它们是**阅读与对拍样本**，不是可直接运行模块。
- 原 `alpha_zoo_adapter.py` 是纯转发 shim（20 行），保留只为记录原 API 形态。

## 为什么删掉它

`alpha_zoo` 占 apps 层 39%（25,669 行 / 876 文件），但全仓只有 **2 处真实调用**：

```
apps/config_runner.py:437  alpha_zoo.compute_as_wide(alpha_id, price_panel)
apps/config_runner.py:451  alpha_zoo.compute_batch(alpha_ids, price_panel)
```

且只在策略配置写 `alpha_id` / `alpha_ids` 时触发。删掉它不影响任何框架功能
（Ticket / 容量闸 / RSI / 能力注册均不依赖），只影响「用 alpha_id 引用预置因子」
这一条业务路径。
