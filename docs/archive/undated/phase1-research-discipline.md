# Phase 1 设计：研究纪律落文（单源评分 · 回测体检 · 交付契约）

> 来源：`refactor-plan-20261008.md` Phase 1（调研借鉴 #2/#3：QuantStudio B1/B2/B3/B4/B8）
> 原则：**observation first**——体检与契约先记录不拦截（与轨迹评估同哲学），激进 gate 留 Phase 5
> 范围：`factor_validate.py`（铁律注记）· `strategy_acceptance/health.py`（新）· `round_manifest.py`（契约）· `runner._finalize_round`（挂载点）
> 状态：设计定稿，待实施

## 1. 现状盘点（读码确认）

| 面 | 现实 |
|---|---|
| 因子质检评分 | `factor_validate`：6 维（stability/diversification/turnover/monotonicity/coverage/rank_ic）+ **`compute_overall_score` 单入口已存在**——B1 要的是**公式 v2 升级**（需量化拍板，本轮不做） |
| 其他"评分" | `templates/prepare.py::_compute_factor_scores`（模板内公式副本）· `config_runner._compute_scores`（**组合构造**，语义不同，铁律明确排除） |
| acceptance | 包内已有 `rules.HardThresholdRule` / `llm_eval` / `decide`——5-gate 体检是**新面**，不动既有判定 |
| manifest | `build_manifest` 已含 verdict/verdict_reason/gates/metrics/levers/predicted_affected——B3 的 **conclusion 枚举是新轴**（研究结论强度 ≠ 回测 keep/discard） |
| metrics 键 | calmar/sharpe/max_dd/ann_ret 稳定；B2 的换手/持仓数/全仓时间**可能不在 metrics**——体检按「有数据才检」实现 |

## 2. 1.1 单源评分铁律（B8 + B1 边界）

**铁律文本**（写入 `factor_validate` 模块 docstring + `extension-points.md` 新红线）：

> 因子质检评分（IC/IR/stability/…/overall）的**唯一真源**是 `core.factor_validate`。
> 任何模块不得内联复制评分公式；需要分数→调用 `compute_overall_score` 及其 6 维。
> **排除**：`config_runner._compute_scores` 是组合权重构造（横截面混合），不是质检评分——不适用本铁律。
> v2 加权公式（quantskills B1）升级时同样只改 `compute_overall_score` 单点。

**本轮动作**：铁律成文 + `templates/prepare.py::_compute_factor_scores` 标注「历史公式副本，铁律豁免待迁移」（模板脚本不阻塞主链，不迁移）。

## 3. 1.2 回测体检 5-gate（B2，observation first）

`core/strategy_acceptance/health.py`（新）：

```python
@dataclass
class Finding:
    check: str          # holdings / turnover / sharpe_ic_range / mdd_range / full_exposure / danger_signal
    status: str         # ok | warn | suspect
    detail: str
    observed: float | None = None

def health_check(metrics: dict, *, prev_metrics: dict | None = None) -> list[Finding]:
    """B2 five-gate backtest health scan (docs/archive/undated/phase1-research-discipline.md).

    Checks fire only when the metric key exists (turnover / holdings /
    full_exposure may be absent from metrics — skipped with status='skip'
    is NOT emitted; absent data = absent check, no noise).
    """
```

| 检查 | 规则（B2） | 状态 |
|---|---|---|
| holdings | 持仓股数 ≤ TopX% | 数据缺→跳过 |
| turnover | 换手 ≤ 2/H | 数据缺→跳过 |
| sharpe_ic_range | Sharpe 或 IC_IR ∈ [0.3, 0.5] 之外→suspect（时序/成交价 bug） | 有即检 |
| mdd_range | MDD ∈ [-20%,-40%] 之外的过浅→suspect（**过浅≈look-ahead**） | 有即检 |
| full_exposure | 全仓时间 >80%（低→信号 NaN） | 数据缺→跳过 |
| **danger_signal** | 年化 >50% **且** MDD >-5% → suspect「**先查 bug 不是庆祝**」 | ann_ret+max_dd 有即检 |

**挂载**（`runner._finalize_round` 内，update_last_metrics 之后）：

```python
findings = health_check(metrics)
if findings:
    runner._emit(session, "study_health_check", {...findings...})
    # manifest 记录：round_manifest.build_manifest 增 findings= 参数（可选，不 break）
```

**不拦截判定**——suspect 只 emit + 入 manifest。观察 2 周后（或 Phase 5）再决定升级为 gate。

## 4. 1.3 交付契约雏形（B3/B4）

`round_manifest.py` 增量：

```python
CONCLUSION_LABELS = frozenset({
    "effective", "weakly", "ineffective", "regime-dependent", "inconclusive",
})  # B3 结论枚举——与 verdict(keep/discard) 正交

REQUIRED_MANIFEST_FIELDS = (...)  # hypothesis / metrics / verdict / levers / predicted_affected / conclusion

def validate_manifest(manifest: dict) -> list[str]:
    """Missing-field warnings (never raises) — gradual enforcement."""
```

- `build_manifest` 增 `conclusion: str = "inconclusive"`（默认最保守）与 `findings: list | None = None`
- finalization 处 `validate_manifest` 缺字段 → logger.warning（不 fail round）
- **渐进**：现存 manifest 不 break；新字段带默认值

## 5. 改动清单

| 文件 | 改动 |
|---|---|
| `core/factor_validate.py` | 模块 docstring 铁律段 |
| `docs/guides/extension-points.md` | 新红线：评分单源 |
| `core/strategy_acceptance/health.py` | 新：Finding + health_check |
| `core/study/round_manifest.py` | CONCLUSION_LABELS / validate_manifest / build_manifest +2 参数 |
| `core/study/runner.py` | `_finalize_round` 挂 health_check emit |
| `templates/prepare.py` | `_compute_factor_scores` 上方标注豁免 |
| `tests/test_research_discipline.py` | 新：5-gate 各 case + danger_signal + validate_manifest + 铁律 rg 检查 |

## 6. 验收

| # | 项 | 条件 |
|---|---|---|
| 1 | 铁律 | doc 成文；`rg compute_overall_score` 副本数不增 |
| 2 | 5-gate | 测试覆盖：normal / mdd 过浅 suspect / danger_signal 触发 / 缺数据跳过 |
| 3 | 挂载 | round 跑完有 `study_health_check` 事件（有 findings 时） |
| 4 | 契约 | validate_manifest 缺 conclusion→warning；build_manifest 不 break（新参数有默认） |
| 5 | 回归 | 69 文件 PR-lane 0 failed；ruff src 保持 0 |
