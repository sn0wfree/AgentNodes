# run_backtest 数据门禁与诊断（Data Readiness Gate）

日期：2026-08-10
状态：已实施
相关模块：`core/data_readiness.py`、`core/agent/builtin_tools/data_tools.py`（check_data）、
`core/agent/builtin_tools/__init__.py`（run_backtest 门禁）、`core/config_runner.py`、
`core/backtest.py`、`core/utils/strategy_engine.py`、`core/agent/role_factory.py`

---

## 背景与根因

`run_backtest(strategy_name='blue_chip_momentum')` 曾连续 14 次返回全 0/NaN 指标
（status=pending），agent 误判「回测引擎未连通」。实测复现的根因链：

1. **幽灵资产混入**：`db.load_price_data` 与 `FactorStrategy._load_long_ohlcv` 查询
   DuckDB 均**不按 `config.codes` 过滤**。策略名下 21 只资产含 6 只历史残留
   （`000001.SZ` 500 行；`600028/600050/601088/601628/601857` 各仅 1 行）
2. **因子在幽灵资产上失败**（close 全 NaN → 表达式求值异常），且失败**只 print 不返回**
3. **score=0 被 `nlargest` 当最优选中**（`fill_value=0` → 幽灵资产 0 分 > 真实资产负分）
4. **inverse_vol 产生 NaN 权重**（`1/NaN`）→ `normalize_weights` 与引擎均不防 NaN
   → nav 变 NaN → `extended_metrics` 全 0/NaN
5. **`metrics.json` 写入非法 JSON**（`json.dump` 默认 `allow_nan=True` → `NaN` 字面量）
6. **status 无条件硬编码 `"pending"`** → agent 无法区分「执行成功」与「未执行」

## 修复策略（三层）

| 层 | 内容 | 位置 |
|----|------|------|
| 事前 | run_backtest 入口数据预检门禁（只读诊断，LLM 自行修复） | `data_readiness.py` + `RunBacktestTool` |
| 事中 | 根因修复：资产过滤 / NaN 防御 / 失败暴露 / 合法 JSON / status | `config_runner.py` / `strategy_engine.py` / `backtest.py` |
| 事后 | 一次性孤儿资产清理脚本 | `scripts/cleanup_orphan_assets.py` |

---

## 一、check_data 工具（独立数据检查工具）

供 `data_quality` 角色 agent 使用；`run_backtest` 门禁复用同一核心。

```
check_data(strategy_name, source="config", codes=None, start_date=None, end_date=None)
- source="config"   : 读 strategies/<name>/config.yaml（codes/start/end 从配置取）
- source="explicit" : 用显式参数检查（无 config 的策略也可查）
- 只读（effects 为空）；返回 _ok({"readiness": {ok, checks[]}})
```

### 检查项

| ID | 名称 | 判定 | fix_hint（给 LLM） |
|----|------|------|--------------------|
| C1 | 资产覆盖 | fail：config.codes 中 DB 无数据的资产 | `get_market_data(codes=[...], strategy_name='<name>')` |
| C2 | 孤儿资产 | warn：DB 名下不在 codes 的资产（代码+行数） | 数据残留，建议重拉数据或忽略 |
| C3 | 窗口/新鲜度 | fail：DB min/max date vs config start/end | `get_market_data(start_date=..., end_date=..., strategy_name=...)` 补齐 |
| C4 | 覆盖密度 | ≤2 行 fail；<min_history warn | 重拉数据或调整 rebalance.min_history |
| C5 | 数据质量 | fail：codes 内资产存在 close 全 NaN 行 | 数据残留，重拉该资产 |
| C6 | 因子语法 | fail：config factors 表达式 `_tokenize` 失败 | 修正 config.yaml factors 表达式 |
| C7 | 行级质量 | warn（仅 include_cleaning=True）：重复行/缺失/异常值 | `clean_data(strategy_name='<name>', preset='standard')` |

**设计要点**
- C2 只管「codes 覆盖」维度；C5 只管「codes 内资产质量」——不重叠
- C6 为语法级检查（`_tokenize` 零数据依赖、100% 覆盖全部因子）；
  数据层因子失败由运行期 `factor_failures` 暴露（见下）
- C7 复用 `clean_data(dry_run=True)` 统计（只读），try/except 防御
- 无 codes 的 config → 覆盖类检查（C1/C3/C4/C5）跳过并 warn
- 报告大小：门禁报告截断（≤2KB，列表前 10 + 等 N 项）；check_data 工具报告
  列表完整（单行截断），供 agent 决策
- **fix_hint 风格**：`strategy_name` 一律用占位符 `'<当前策略名>'`（模板语义
  明确，策略名在 agent 上下文中已知），并附「为什么」的缺口诊断（如
  「DB 自 2022-12-09 起，配置要求自 2020-01-01 起」）——让 LLM 理解意图后
  自主决策（拉数据 or 改 config），而非机械照抄参数

## 二、run_backtest 内置门禁

`RunBacktestTool.execute` 读 config 后、创建 run 目录前执行 C1~C6（轻量，不含 C7）：
- `not ok` → `err_actionable`（error 文本带**后果说明**，如「缺 5 只资产 → 策略实际
  仅覆盖 10/15，结果失真」；`extra.readiness` 附截断报告；fix 指引
  `check_data` → `get_market_data`/`clean_data` → 重试），**不创建 run**
- `ok`（可含 warn）→ 正常执行；返回附 `readiness` 摘要与运行期 `factor_failures`
- 门禁拦的是「**结果不可信**」而非「崩溃」——崩溃兜底由 NaN 防御负责

## 二点五、工具错误标准化 + 组合式拆分（v1.2.0）

### tool_errors 装饰器（`core/agent/tools.py`）
- `tool_errors` 装饰 execute：`raise ToolError` → 自动注入 tool 名 → 确定性 JSON；
  dict 返回值 → 统一 JSON 序列化；非 transient 异常 → 结构化兜底；
  transient（ValueError/TypeError/…）→ re-raise 交给框架重试
- `BaseTool.__init_subclass__` 自动包装所有子类 execute（零遗漏、防重包、
  `functools.wraps` 保持签名）
- 职责边界：invoke 管框架层（参数 coerce/权限/transient 重试）不动；
  装饰器管 execute 业务层
- `ToolError` 增加 `step` 与 `extra` 字段

### RunBacktestTool 拆分为组合步骤（不注册，仅编排）
```
RunBacktestTool.execute（编排器）
  ├─ ConfigLoadStep      读 config → cfg / ToolError(step='config_load')
  ├─ DataPrepareStep     数据准备: 执行 load_data（source=auto/auto+duckdb 时
  │                      在线获取写 DB）→ 门禁检查的才是最终回测数据
  ├─ DataReadinessStep   门禁 C1~C6 / ToolError(step='data_gate', extra=readiness)
  └─ EngineRunStep       run_backtest_from_yaml / ToolError(step='engine_run')
```
- 每步继承 BaseTool（自动获得标准化错误），错误带 step 标识精确定位环节
- 存量工具（err_actionable 手动模式）渐进迁移，本次只迁移 run_backtest 链
- 契约测试（test_tool_contract）同步更新

## 三、运行期失败暴露

`compute_weights` 内因子失败此前只 print 到 stdout（agent 不可见），现全部收集：

- `FactorStrategy.factor_failures`：`{factor, asset, error, occurrences}`（按因子+资产去重）
  - 覆盖：因子计算失败 / 因子异常 / Alpha Zoo 失败 / 组合失败 / 表达式因子跳过
- `BacktestResult.factor_failures` 字段携带（`field(default_factory=list)`）
- `run_backtest_from_yaml`：
  - 完整原始列表写 `run_dir/factor_failures.json`（与脚本路径既有格式统一，可 read_file 审计）
  - `metrics.json` 存聚合摘要 `factor_failures_summary`（按因子聚合 ≤5 条 + 等 N 条）
  - 运行期其他警告（如 equity_curve.csv 导出失败）收集进 `metrics["warnings"]`
- 工具返回 payload 附 `factor_failures`（聚合摘要）与 `warnings`

## 四、根因修复清单

| 位置 | 改动 |
|------|------|
| `config_runner.load_data` | 面板按 `config.codes` 过滤列 |
| `config_runner.FactorStrategy` | 构造加 `codes`；`_load_long_ohlcv` 查询 `AND asset_code IN (...)` |
| `config_runner.compute_weights` | scores `replace(±inf,NaN).dropna()` 后 nlargest；空返回 `{}` |
| `config_runner.compute_weights` | inverse_vol `vols.dropna()` 后算；全 NaN → 等权 |
| `strategy_engine.run` | 权重过滤 NaN（最后防线，nav 永不为 NaN） |
| `backtest.py` | `_clean_nan`（NaN/Inf→null 递归）；`save_run_metrics` 与返回前清理 |
| `backtest.py` | status `pending→success`（两条路径 + `save_backtest_result`） |
| `compute_factor.py` | 输入全 NaN → 提前报「数据不足」 |

## 五、status 语义变化

- 成功执行 → `status: "success"`（此前无条件 `"pending"`，误导 agent）
- `results.tsv` 第 12 列原为 autoresearch keep/discard/pending 三态——flip 逻辑
  直接改列值不受影响，但该列现可能写 `success`；报表语义以 run_card/metrics.json 为准

## 六、数据源说明（实测环境）

- DuckDB 文件：`<workspace>/data.duckdb`
- 数据写入幂等（`INSERT OR REPLACE` 按 strategy+asset+date），不会覆盖旧资产——
  孤儿资产清理由 `scripts/cleanup_orphan_assets.py` 处理（备份 .bak → 按 config codes
  DELETE → 无 config 策略跳过）
- `source: auto` 在线抓取失败时旧逻辑静默回退缓存数据；门禁 C3 现会显式拦截窗口不足

## 测试

- `tests/test_data_readiness.py`：C1~C7 场景、无 codes 跳过、截断、门禁 fail 不建 run
- `tests/test_check_data_tool.py`：参数校验、config/explicit、返回结构、effects 只读
- `test_config_runner_import` / `test_strategy_engine` / `test_backtest` 扩展：
  资产过滤、NaN 防御、factor_failures 收集与落盘、JSON 合法、status success
- 回归：`test_agent_tools` / `test_agent_loop` / `test_chat_loop` / `test_e2e_full_flow` /
  `test_engine_regression` / `test_role_factory`
