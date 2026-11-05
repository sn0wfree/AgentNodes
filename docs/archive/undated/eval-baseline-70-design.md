# Eval Baseline 升级：10 → 70 题全集

> 来源：`refactor-plan-20261008.md` Phase 0 遗留（「70 题升级在 nightly 门禁稳定后」）· 本次触发
> 现状：`evals/nightly-baseline.json` 10 题高区分子集（mean 0.99 / pass 1.0 / steps_mean 2.3）
> 目标：70 题全集 baseline，轨迹评估安全网覆盖完整 golden 网

## 1. 升级动机

| 维度 | 10 题 | 70 题 |
|---|---|---|
| 信号覆盖 | injection/safety/budget/data/reason 5 类高区分 | **全 tags 覆盖**（含 multi/error/review/meta/vcs/write/skills 等） |
| steps_mean 稳定性 | n=10 噪声大 | n=70 稳定 |
| 重构漂移检测 | 子集可能漏漂移 | **全集漂移可查** |
| API 成本 | ~1-2 分钟 | ~7-15 分钟（529 限流可能重试） |

**阈值不变**：T1（steps_mean 30%）/ T2（序列 20% difflib<0.5）/ T3（新非 stop 终态）——都是相对/比例阈值，与题数无关。70 样本下更稳（减少误报）。

## 2. 执行

```bash
python evals/run_evals_llm.py --max-iterations 5 --json > scorecard.json 2>evals\_p0_run.log
python scripts/check_eval_regression.py --scorecard scorecard.json --update-baseline
# 验证：
python scripts/check_eval_regression.py --scorecard scorecard.json   # 自检 Δ0
```

- **全 70 题**（`--only` 不传）
- `--max-iterations 5`（沿用 Phase 0 预算）
- 进度行走 stderr，JSON 落盘（Phase 0 修复的 stdout 纯度保证）

## 3. 验收

| # | 项 | 条件 |
|---|---|---|
| 1 | baseline 覆盖 | n=70，trajectory 字段全（steps_mean/p90/tool_calls_mean/terminal） |
| 2 | 自检 | check_eval（同 scorecard）Δ0，无 REGRESSION |
| 3 | T1-T3 逻辑 | 阈值不变（无 --max-steps-drift 覆盖） |
| 4 | 提交 | baseline 更新 + 记录（mean/pass@0.7/终态分布） |

## 4. 风险

- API 限流（529）重试增多 → 跑超时预期 15 分钟；用 `PYTHONWARNINGS=ignore` 减噪
- 70 题 mean 可能低于 10 题子集（更多低区分题）——**阈值基于 delta 而非绝对**，mean 变化不影响判定
