# 样本 01 — YAML 配置驱动的端到端回测

## 这个样本验证什么

业务侧（quantnodes）移走 `apps/` 之后，AgentNodes 框架侧必须仍然能回答：

1. `run_backtest` 工具能否从一份 YAML 配置跑完全链路
   （加载配置 → 准备数据 → 就绪门 → 引擎执行 → 写 runs/ + DuckDB）
2. DataStore 接缝（`foundation.storage.register_default_data_store`）是否仍被正确接线
3. 因子表达式 `ts_return(close, 20)` / `ts_std(...)` 的语义有没有变
4. 回测产物（run card / nav / weights）的字段契约有没有变

## 目录

```
01_yaml_backtest/
├── README.md          本文件
├── input/
│   ├── strategy.yaml  回测配置（从 templates/config.yaml 裁剪为可独立运行的最小集）
│   ├── strategy.py    策略实现（来自 templates/strategy.py）
│   ├── prepare.py     数据准备/指标输出（来自 templates/prepare.py）
│   └── data/          合成 OHLCV（CSV，随仓库提交，不依赖网络）
├── run.py             可执行入口：建临时 workspace → 跑 → 与 expected 对拍
└── expected/
    └── metrics.json   金标准指标（sharpe/calmar/max_dd/turnover 等）
```

## 怎么跑

```bash
# 单独跑（需要已安装业务侧 quantnodes 或本样本自带的 golden apps 副本）
python testcase/samples/01_yaml_backtest/run.py

# 只验证样本资产本身（不需要业务代码）
python testcase/samples/01_yaml_backtest/run.py --check-assets
```

输出 `PASS` / `FAIL <差异明细>`。

## 为什么它不会过期得太快

- 输入是**合成的确定性数据**（固定 seed），不连网络、不依赖 token
- 期望输出是**指标区间**而非精确小数（浮点容差 1e-6 + 区间断言）
- 配置只覆盖最小必需字段，框架加字段不会让它失效

## 当前状态

- [x] 目录与输入资产（strategy.py / prepare.py 已从 `templates/` 复制）
- [ ] `strategy.yaml` 裁剪
- [ ] 合成 OHLCV 数据
- [ ] `run.py` + 对拍
- [ ] `expected/metrics.json` 首次基线
