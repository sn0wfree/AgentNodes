---
name: ml-strategy
category: strategy
description: ML 预测策略 — 特征工程 + 标签构建 + Walk-forward 训练 + OOS 验证
tags: [ml, machine-learning, walk-forward, classification, regression]
---

# Machine Learning Predictive Strategy

借鉴自 vibe-trading ml-strategy skill，适配 strategy-research 框架。

## 工作流

1. **数据验证**: 检查 OHLCV 列、最小行数、NaN 比例
2. **特征工程**: 从原始 OHLCV 提取多维因子
3. **标签构建**: 未来 N 日收益 >0 为正类 (`1`)，<0 为负类 (`0`)
4. **Walk-forward 训练**: 仅用历史数据训练，逐日滚动预测
5. **信号生成**: `predict_proba[:, 1]` 映射到 `[-1.0, 1.0]`

## 特征工程参考

| 特征名 | 公式 | 含义 |
|--------|------|------|
| ret_5d | `close.pct_change(5)` | 过去 5 日收益 (短期动量) |
| ret_20d | `close.pct_change(20)` | 过去 20 日收益 (中期动量) |
| vol_20d | `returns.rolling(20).std()` | 20 日波动率 |
| rsi_14 | RSI 公式 | 相对强弱指数 |
| ma_ratio | `close / close.rolling(20).mean()` | 偏离 20 日均线程度 |
| volume_ratio | `volume / volume.rolling(20).mean()` | 量比 |
| bb_position | `(close - bb_lower) / (bb_upper - bb_lower)` | 布林带位置 |
| high_low_ratio | `(high - low) / close` | 日内振幅比 |
| close_open_ratio | `(close - open) / open` | 日内收益率 |
| skew_20d | `returns.rolling(20).skew()` | 收益偏度 |

## 模型选择

| 模型 | 优点 | 缺点 | 适用场景 |
|------|------|------|----------|
| RandomForestClassifier | 不易过拟合，超参数鲁棒 | 捕捉趋势特征较弱 | 默认首选 |
| GradientBoostingClassifier | 高精度，捕捉非线性 | 易过拟合，训练慢 | 数据充足时 |
| Ridge / LogisticRegression | 训练快，可解释 | 仅捕捉线性关系 | 快速基线 |

## 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| model_type | `"random_forest"` | 模型类型 |
| min_train_size | 252 | 最小训练集大小 |
| retrain_freq | 20 | 重训练频率 (每 N 交易日) |
| prediction_horizon | 5 | 预测horizon (未来 N 日收益) |
| n_estimators | 100 | 树数量 |
| max_depth | 5 | 最大树深 |
| window_type | `"expanding"` | 训练窗口类型 |
| sliding_size | 504 | 滑动窗口大小 |

## Walk-forward 训练核心

```python
def walk_forward_predict(features, labels, min_train_size=252,
                         retrain_freq=20, model_type="random_forest"):
    predictions = pd.Series(0.0, index=features.index)
    model = None
    scaler = None

    for i in range(min_train_size, len(features)):
        if model is None or (i - min_train_size) % retrain_freq == 0:
            X_train = features.iloc[max(0, i-504):i].values
            y_train = labels.iloc[max(0, i-504):i].values
            valid = ~(np.isnan(X_train).any(axis=1) | np.isnan(y_train))
            X_train, y_train = X_train[valid], y_train[valid]
            if len(X_train) < 50:
                continue
            scaler = StandardScaler()
            X_train = scaler.fit_transform(X_train)
            model = RandomForestClassifier(n_estimators=100, max_depth=5)
            model.fit(X_train, y_train)

        X_today = features.iloc[i:i+1].values
        if np.isnan(X_today).any():
            continue
        X_today = scaler.transform(X_today)
        prob = model.predict_proba(X_today)[0, 1]
        predictions.iloc[i] = prob * 2 - 1

    return predictions.fillna(0.0).clip(-1.0, 1.0)
```

## 常见陷阱

1. **过拟合**: 树太深 (`max_depth > 10`)、特征太多、训练集太小
2. **类别不平衡**: 牛市中涨跌比可能 7:3，用 `class_weight="balanced"`
3. **前视偏差**: 用 T 日 close 计算特征预测 T 日信号 → 需用 T-1 及之前数据
