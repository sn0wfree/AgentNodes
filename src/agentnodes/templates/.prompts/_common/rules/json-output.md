# JSON 输出约定（详细版）

> 适用于返回结构化输出的角色：**researcher / strategist / backtest_diagnostics / orchestrator / critic**。
>
> **简短版本**已内联到这 5 个角色文件的顶部指针处。本文件是完整规则 + 各角色 schema 详解。

## 通用规则（5 个 JSON role 共享）

1. **必须返回纯 JSON**，不包含任何其他文本、解释或 markdown 代码块标记
2. **以 `{` 开头，`}` 结尾**
3. 字段值缺失时留 `null` 或 `"未测"`，不写占位字符串
4. 数值字段使用真实数字，不编造
5. 字段 schema 见各角色的"输出格式"段

## 错误的输出

### ❌ 反模式 1：包裹在 markdown 代码块中

```markdown
以下是结果：

\`\`\`json
{"action": "search_external", ...}
\`\`\`

希望对你有帮助。
```

**禁止**。必须纯 JSON，无其他文字。

### ❌ 反模式 2：解释性前缀

```json
// 我分析了因子状态，建议搜索外部因子...
{"action": "search_external", ...}
```

**禁止**。注释和解释都不允许。

### ❌ 反模式 3：编造字段值

```json
{"calmar_improvement": 0.15, "sharpe_improvement": 0.20}
```

（数字从哪来？**禁止编造**）

## 正确的输出

```json
{"action":"search_external","hypothesis":"因子覆盖度不足","reason":"当前仅覆盖动量维度","predicted_affected":["calmar","sharpe"],"avoid_actions":[],"factor_direction":"波动率","bias_check":{"leader_bias":"pass","english_bias":"pass","narrative_bias":"pass","confirmation_bias":"pass","recency_bias":"pass"}}
```

紧凑、无多余内容、字段值为真实数字或合理标记。

### 示例

**例 1**: 字段缺失处理

- 假设 researcher 无法确定 bias_check 的某个值
- ❌ 错误：`{"narrative_bias": "pass"}`（编造）
- ✅ 正确：`{"narrative_bias": "未测", "reason": "数据不足"}`

**例 2**: 数值必须真实

- ❌ 错误：`{"confidence": 0.95}`（猜测）
- ✅ 正确：`{"confidence": 0.85}`（来自工具返回值）

## 各角色 schema 详解

### researcher.md

```json
{
  "action": "search_external | discover_local | optimize_param | remove_factor",
  "hypothesis": "一句话描述假设",
  "reason": "决策依据",
  "predicted_affected": ["calmar", "sharpe"],
  "avoid_actions": ["已失败的 action 列表"],
  "factor_direction": "目标因子类型",
  "bias_check": {
    "leader_bias": "pass | fail",
    "english_bias": "pass | fail",
    "narrative_bias": "pass | fail",
    "confirmation_bias": "pass | fail",
    "recency_bias": "pass | fail"
  }
}
```

### strategist.md

```json
{
  "action": "integrate | optimize | remove",
  "changes": [
    {"param": "FACTOR_EXPRS", "old": [...], "new": [...]},
    {"param": "top_n", "old": 10, "new": 20}
  ],
  "reason": "操作原因",
  "expected_impact": "预期影响",
  "predicted_affected": ["calmar", "sharpe"]
}
```

### backtest_diagnostics.md

```json
{
  "error_type": "runtime_error | logic_bug | none",
  "severity": "critical | warning | info",
  "symptom": "零交易",
  "root_cause": "信号逻辑条件太严格",
  "fix_suggestion": "放宽 ts_return(close, 20) 的阈值",
  "confidence": 0.85
}
```

### orchestrator.md

```json
{
  "step": 1,
  "status": "completed",
  "output": {...}
}
```

### critic.md

```json
{
  "verdict": "keep | discard",
  "analysis": "分析原因",
  "risk_rating": "Green | Yellow | Red",
  "direction": "exploit | explore | diversify",
  "risk_metrics": {
    "var_95": -0.021,
    "cvar_95": -0.034,
    "max_drawdown": -0.125,
    "sharpe_significance": "significant (p<0.05)"
  },
  "attribution": {
    "alpha": 0.008,
    "beta_mkt": 0.85,
    "sector_allocation": "+0.5%",
    "stock_selection": "+0.9%"
  },
  "suggestions": ["建议1", "建议2"],
  "risk_warnings": ["警告1"]
}
```

## 字段缺失处理

| 场景 | 处理方式 |
|---|---|
| 不知道值 | `null` 或 `"未测"` |
| 不适用该字段 | 省略字段（不要写 `null`） |
| 数据未返回 | `"未测"` 字符串 + reason 字段说明 |

**禁止**：用 `0`、`""`、`"N/A"` 等占位符冒充真实值。

## 何时读本文件

- 第一次以 JSON role 启动
- 不确定某个字段应该填什么
- 输出被框架拒绝（schema 校验失败）

## 何时**不**读本文件

- 已经在该角色多次输出 JSON，流程熟悉
- 字段含义清楚，无需对照 schema