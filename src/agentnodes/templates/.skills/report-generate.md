---
name: report-generate
category: tool
description: 报告生成 — 把研究结果输出为可读 Markdown / JSON / Pine Script / PDF
tags: [report, markdown, pdf, pine-script, documentation]
---

# Report Generation

研究完成后, 需要把结论沉淀为可审计、可分享的报告。本 skill 教 LLM 如何生成不同格式的报告。

## 报告类型

| 类型 | 受众 | 格式 | 重点 |
|------|------|------|------|
| 内部研究笔记 | 团队 | Markdown | 设计/假设/证据 |
| 回测报告 | 团队 | Markdown + JSON | 指标/参数/敏感度 |
| 客户交付 | 客户/PM | Markdown + 图表 | 收益/风险/归因 |
| 代码审计 | 审计 | Markdown + diff | 改动/原因 |
| 跨项目对比 | 决策者 | Markdown table | 优缺点/推荐 |

## Markdown 模板 (回测报告)

```markdown
# 策略: momentum_reversal_blend_v2

**作者**: <agent or human>
**日期**: 2025-10-15
**状态**: VALIDATED / PROPOSED / REJECTED

## 摘要

3-5 句话总结: 这策略做什么、表现如何、为什么用。

## 设计动机

基于哪些假设 / 文献 / 前期研究。

## 方法

- 数据: A 股, 2018-2024
- 因子: 短期反转 (5d) + 长期动量 (60d) + 低波动过滤
- 权重: IC 加权
- 调仓: 月频

## 结果

| 指标 | 数值 | 基准 | 差异 |
|------|------|------|------|
| 年化 | +12.5% | +8.2% | +4.3% |
| 夏普 | 1.05 | 0.62 | +0.43 |
| 最大回撤 | -8.2% | -15.4% | +7.2% |
| 胜率 | 58% | 52% | +6% |
| 换手 | 5.2x | - | - |

## 风险

- 流动性: 单股最大仓位 2%
- 集中度: 行业暴露中性化
- 极端行情: 2020 年回撤超预期

## 验证

- 样本外 2023-2024: 夏普 0.92 (稳健)
- 跨市场: H 股 / 美股类似表现 (中)
- Walk-forward: 5/5 通过

## 结论

✅ 建议上线, 仓位 5% 总组合权重。

## 附件

- `runs/run_0042/metrics.json`
- `runs/run_0042/run_card.json`
- `runs/run_0042/strategy.py` (snapshot)
```

## Pine Script 导出

策略通过验证后, 可转为 TradingView Pine Script:

```bash
quantnodes-research export <workspace> \
  --strategy momentum_reversal_blend_v2 \
  --format pine \
  --output strategy.pine
```

输出示例:

```pine
//@version=5
strategy("momentum_reversal_blend_v2", overlay=true)

// Inputs
top_n = input.int(10, "Top N")
max_weight = input.float(0.25, "Max Weight")
rebalance_freq = input.int(20, "Rebalance Frequency")

// Factors
mom_60 = ta.roc(close, 60)
rev_5 = -ta.roc(close, 5)
vol_20 = ta.stdev(ta.change(close), 20)

// Score
score = (0.4 * rev_5 + 0.4 * mom_60 - 0.2 * vol_20)

// ... 完整代码
```

## PDF 报告 (可选, 需额外依赖)

```python
# 使用 WeasyPrint 或 ReportLab
from weasyprint import HTML
html_content = render_markdown_to_html(md_text)
HTML(string=html_content).write_pdf("report.pdf")
```

## JSON 输出 (机器可读)

```json
{
  "strategy_id": "strat_abc123",
  "version": "v2",
  "created_at": "2025-10-15T10:00:00Z",
  "metrics": {
    "annual_return": 0.125,
    "sharpe": 1.05,
    "max_drawdown": -0.082,
    "calmar": 1.52,
    "win_rate": 0.58,
    "turnover": 5.2
  },
  "factors": [
    {"name": "rev_5", "weight": 0.4, "ic": 0.025},
    {"name": "mom_60", "weight": 0.4, "ic": 0.032},
    {"name": "vol_filter", "weight": 0.2, "ic": 0.018}
  ],
  "validation": {
    "oos_years": 2,
    "oos_sharpe": 0.92,
    "walk_forward_passes": "5/5",
    "monte_carlo_p_value": 0.03
  },
  "artifacts": [
    "runs/run_0042/metrics.json",
    "runs/run_0042/strategy.py"
  ]
}
```

## 与现有系统集成

| 命令 | 用途 |
|------|------|
| `quantnodes-research run` | 跑回测, 生成 metrics.json |
| `quantnodes-research validate-run` | 统计验证 |
| `quantnodes-research export` | 转 Pine/TDX/vnpy |
| `quantnodes-research accept` | 验收决策 |
| `quantnodes-research hypothesis update` | 更新假设状态 |

## 报告命名约定

- `runs/<run_id>/report.md` — 自动生成的回测报告
- `runs/<run_id>/run_card.{json,md}` — Trust Layer 摘要
- `<workspace>/reports/<strategy>_<date>.md` — 研究笔记
- `<workspace>/decisions/<decision_id>.md` — 决策记录

## 写作建议

1. **结论先行**: 第一段说"该策略是 X, 表现 Y, 建议 Z"
2. **数据说话**: 表格 > 段落, 数字 > 形容词
3. **诚实失败**: 列出所有尝试的失败案例
4. **可重现**: 参数、数据、代码都附上
5. **审计友好**: 写明决策时点和当时的市场背景

## 输出

最终输出 JSON manifest:

```json
{
  "report_files": [
    "runs/run_0042/report.md",
    "runs/run_0042/run_card.json",
    "exports/momentum_v2.pine"
  ],
  "size_bytes": 45632,
  "generated_at": "2025-10-15T10:30:00Z"
}
```