"""
{strategy_name} 策略配置。
Agent 可以修改: PARAMS, FACTOR_EXPRS, FACTOR_WEIGHT_METHOD
"""

# ============================================================
# 策略参数 (Agent 可改)
# ============================================================
PARAMS = {
    "top_n": 10,                   # 持有 top-N 资产
    "max_weight": 0.25,            # 单资产最大权重 25%
    "rebalance_freq": 20,          # 每月调仓 (≈20 交易日)
}

# ============================================================
# 因子表达式 (Agent 可改)
# ============================================================
FACTOR_EXPRS = [
    # 默认: 中长期动量因子 (ts_mean 20日 / ts_mean 60日 - 1)
    # Agent 可在此基础上迭代: 增加 / 替换 / 删除因子
    {
        "factor_name": "momentum_20_60",
        "factor_code": "ts_mean(close, 20) / ts_mean(close, 60) - 1",
        "weight": 1.0,
    },
]

# ============================================================
# 因子权重方式 (Agent 可改)
# ============================================================
FACTOR_WEIGHT_METHOD = "equal"  # "equal" | "inv_vol"

# ============================================================
# 以下不改
# ============================================================
if __name__ == "__main__":
    import sys
    from pathlib import Path
    # 将当前目录添加到 sys.path 以便导入 prepare
    sys.path.insert(0, str(Path(__file__).parent))
    import prepare
    data = prepare.load_data()
    metrics = prepare.evaluate(PARAMS, FACTOR_EXPRS, FACTOR_WEIGHT_METHOD, data)
    # 结构化指标契约：框架优先读取 metrics_raw.json（run.log 正则仅作降级）。
    prepare.save_metrics(metrics)
    print("---")
    for k, v in metrics.items():
        if isinstance(v, bool):
            print(f"{k}: {v}")
        elif isinstance(v, int):
            print(f"{k}: {v}")
        else:
            print(f"{k}: {v:.6f}")
