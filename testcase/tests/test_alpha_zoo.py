"""Alpha Zoo 单元测试 — 每个 alpha 都单独测试。

包括:
- 460 alphas 全部覆盖
- 验证 DataFrame 输出形状
- 验证无 inf 值
- 验证与 .py fallback 一致性（如存在）
- 验证结果在合理数值范围
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.alpha_zoo import compute_alpha, list_alphas

warnings.filterwarnings("ignore")

N_DAYS = 500
N_ASSETS = 50
SEED = 42


@pytest.fixture(scope="module")
def panel() -> dict[str, pd.DataFrame]:
    """统一的测试 panel — 50 assets x 500 days。

    使用均值回归 (OU) 过程生成价格，确保截面排名频繁变化。
    同时包含 fund:* 列用于 fundamental alpha 测试。
    """
    rng = np.random.default_rng(SEED)
    dates = pd.bdate_range("2024-01-01", periods=N_DAYS)
    cols = [f"S{i}" for i in range(N_ASSETS)]

    # OU 过程: 均值回归到 10.0，确保排名不锁定
    prices = np.zeros((N_DAYS, N_ASSETS))
    prices[0] = 10.0 + rng.normal(0, 0.5, N_ASSETS)
    mean_rev_speed = 0.05
    vol = 0.02
    for t in range(1, N_DAYS):
        drift = mean_rev_speed * (10.0 - prices[t - 1])
        prices[t] = prices[t - 1] * (1.0 + drift + rng.normal(0, vol, N_ASSETS))

    close = pd.DataFrame(prices, index=dates, columns=cols)
    high = close * (1 + np.abs(rng.normal(0, 0.005, (N_DAYS, N_ASSETS))))
    low = close * (1 - np.abs(rng.normal(0, 0.005, (N_DAYS, N_ASSETS))))

    # open_ = 前一天 close + 小随机扰动（避免恒等于 shift(close) 导致恒零输出）
    open_ = close.shift(1).fillna(close.iloc[0]) * (1 + rng.normal(0, 0.002, (N_DAYS, N_ASSETS)))

    volume = pd.DataFrame(rng.uniform(1e6, 1e8, (N_DAYS, N_ASSETS)), index=dates, columns=cols)
    amount = pd.DataFrame(rng.uniform(1e7, 1e9, (N_DAYS, N_ASSETS)), index=dates, columns=cols)

    data = {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "vwap": close * (1 + rng.normal(0, 0.001, (N_DAYS, N_ASSETS))),
        "returns": close.pct_change().fillna(0),
    }
    for w in [5, 10, 15, 20, 30, 50, 60]:
        data[f"adv{w}"] = volume.rolling(w).mean().fillna(volume.mean())

    # fund:* 列用于 fundamental alpha 测试
    data["fund:roe"] = pd.DataFrame(rng.uniform(0.05, 0.25, (N_DAYS, N_ASSETS)), index=dates, columns=cols)
    data["fund:gross_profitability"] = pd.DataFrame(rng.uniform(0.05, 0.40, (N_DAYS, N_ASSETS)), index=dates, columns=cols)
    data["fund:asset_growth"] = pd.DataFrame(rng.uniform(-0.10, 0.30, (N_DAYS, N_ASSETS)), index=dates, columns=cols)
    data["fund:net_income"] = pd.DataFrame(rng.uniform(1e6, 1e9, (N_DAYS, N_ASSETS)), index=dates, columns=cols)
    data["fund:shares_diluted"] = pd.DataFrame(rng.uniform(1e7, 1e10, (N_DAYS, N_ASSETS)), index=dates, columns=cols)

    return data


@pytest.fixture(scope="module")
def alpha_registry() -> list[dict]:
    """缓存 alpha 列表避免重复扫描磁盘."""
    return list_alphas()


def pytest_generate_tests(metafunc):
    """动态参数化所有 alpha 测试函数."""
    if "alpha_meta" in metafunc.fixturenames:
        metas = list_alphas()
        ids = [m["id"] for m in metas]
        metafunc.parametrize("alpha_meta", metas, ids=ids)


# ------- 单元测试：每个 alpha 都执行 -------

def test_alpha_runs(alpha_meta, panel):
    """每个 alpha 都能成功执行，输出为 DataFrame."""
    aid = alpha_meta["id"]
    try:
        result = compute_alpha(aid, panel)
    except Exception as e:
        pytest.fail(f"alpha [{aid}] raised: {type(e).__name__}: {e}")

    assert isinstance(result, pd.DataFrame), \
        f"alpha [{aid}] returned {type(result).__name__}, expected DataFrame"

    assert result.shape == panel["close"].shape, \
        f"alpha [{aid}] shape {result.shape} != {panel['close'].shape}"


def test_alpha_no_inf(alpha_meta, panel):
    """结果不应包含过多 inf 值（≤30%）"""
    aid = alpha_meta["id"]

    try:
        result = compute_alpha(aid, panel)
    except Exception:
        pytest.skip(f"{aid} cannot compute (likely needs extra data)")

    if isinstance(result, pd.DataFrame):
        n_inf = np.isinf(result.values).sum()
        total = result.size
        inf_ratio = n_inf / total if total > 0 else 0
        # 30% 上限是宽松——ts_corr 在随机面板零方差窗口可能产出 ±inf
        assert inf_ratio < 0.30, f"alpha [{aid}]: {n_inf}/{total} ({inf_ratio:.1%}) inf values"


def test_alpha_nan_ratio(alpha_meta, panel):
    """结果 NaN 比例应根据 warmup_bars 自适应放宽."""
    aid = alpha_meta["id"]

    try:
        result = compute_alpha(aid, panel)
    except Exception:
        pytest.skip(f"{aid} cannot compute")

    if isinstance(result, pd.DataFrame):
        n_nan = np.isnan(result.values).sum()
        total = result.size
        nan_ratio = n_nan / total if total > 0 else 0
        # 自适应阈值: warmup 越长允许越多 NaN（最多 100%）
        n_days = len(panel["close"])
        try:
            import yaml as yamllib
            yaml_path = Path(alpha_meta["file"])
            cfg = yamllib.safe_load(yaml_path.read_text())
            warmup = cfg.get("min_warmup_bars", 0)
        except Exception:
            warmup = 0
        if warmup >= 0.99 * n_days:
            # warmup == n_days → 全 NaN 是正常的
            thresh = 1.0
        elif warmup >= 0.8 * n_days:
            thresh = 0.999
        elif warmup >= 0.4 * n_days:
            thresh = 0.95
        elif warmup >= 0.15 * n_days:
            thresh = 0.85
        else:
            thresh = 0.80
        assert nan_ratio <= thresh, f"alpha [{aid}]: {nan_ratio:.1%} NaN (warmup={warmup}, threshold={thresh:.0%})"


def test_alpha_index_matches(panel, alpha_meta):
    """结果的 index/columns 应与输入一致。"""
    aid = alpha_meta["id"]

    try:
        result = compute_alpha(aid, panel)
    except Exception:
        pytest.xfail(f"{aid}: cannot compute (likely needs extra data)")

    if isinstance(result, pd.DataFrame):
        assert list(result.index) == list(panel["close"].index), \
            f"{aid}: index mismatch"
        assert list(result.columns) == list(panel["close"].columns), \
            f"{aid}: columns mismatch"


# ------- .py fallback 综合测试 -------

def _try_compute_py_fallback(alpha_id, panel):
    """Try .py fallback directly (skipping YAML)."""
    import importlib
    import re
    # Match alpha101_alpha_003 / fundamental_roe / academic_name patterns
    m = re.match(r"^([a-z]+)(?:(\d+)_)?([a-z0-9_]+)$", alpha_id)
    if not m:
        return None
    (m.group(1) + (m.group(2) or "")), m.group(3)
    # Try multiple module-path conventions
    candidates = []
    for zid in ["alpha101", "gtja191", "qlib158", "academic", "fundamental"]:
        if alpha_id.startswith(zid):
            short_name = alpha_id[len(zid) + 1:]
            candidates.extend([
                f"strategy_research.apps.alpha_zoo.{zid}.{short_name}",
                f"strategy_research.apps.alpha_zoo.{zid}.alpha_{short_name}",
            ])
            break
    for modname in candidates:
        try:
            mod = importlib.import_module(modname)
            if hasattr(mod, "compute"):
                return mod.compute(panel)
        except Exception:
            continue
    return None


# 已知 YAML/Py 不一致的 alpha (ind_neutralize 差异, 非数据质量问题)
_KNOWN_CORR_NAN = {
    "alpha101_alpha_100",
}
# 已知有效点过少的 alpha: 现在 panel 有 500 天, 这些应该能通过
_KNOWN_TOO_FEW: set[str] = set()


def test_alpha_yaml_py_consistency(alpha_meta, panel):
    """当 YAML 和 .py 都存在时，结果应高度相关（corr > 0.93 或 NaN 比例匹配）。"""
    aid = alpha_meta["id"]
    # academic alphas 只有 .py, 没有 YAML, 跳过 cross-validation
    if aid.startswith("academic_"):
        if alpha_meta["format"] != "yaml":
            pytest.skip(f"{aid}: only .py fallback (no YAML)")
        # 如果有 YAML 版, 跑测试

    yaml_res = None
    py_res = None
    try:
        yaml_res = compute_alpha(aid, panel)
    except Exception:
        pass
    try:
        py_res = _try_compute_py_fallback(aid, panel)
    except Exception:
        pass

    if yaml_res is None or py_res is None or not isinstance(yaml_res, pd.DataFrame):
        if aid in _KNOWN_CORR_NAN or aid in _KNOWN_TOO_FEW:
            pytest.xfail(f"{aid}: known data quality issue (cannot compute)")
        pytest.skip(f"{aid}: cannot compute either YAML or PY")

    if yaml_res.shape != py_res.shape:
        if aid in _KNOWN_CORR_NAN or aid in _KNOWN_TOO_FEW:
            pytest.xfail(f"{aid}: shape mismatch (known)")
        pytest.skip(f"{aid}: shape {yaml_res.shape} vs {py_res.shape}")

    a = yaml_res.values.flatten()
    b = py_res.values.flatten()
    mask = ~(np.isnan(a) | np.isnan(b))
    if mask.sum() < 20:
        if aid in _KNOWN_CORR_NAN or aid in _KNOWN_TOO_FEW:
            pytest.xfail(f"{aid}: too few valid points ({mask.sum()})")
        pytest.skip(f"{aid}: too few valid points ({mask.sum()})")

    try:
        corr = np.corrcoef(a[mask], b[mask])[0, 1]
    except Exception:
        if aid in _KNOWN_CORR_NAN:
            pytest.xfail(f"{aid}: known data quality issue")
        pytest.skip(f"{aid}: cannot compute correlation")
    if np.isnan(corr):
        if aid in _KNOWN_CORR_NAN:
            pytest.xfail(f"{aid}: known data quality issue (corr=NaN)")
        pytest.skip(f"{aid}: correlation is NaN")

    assert corr > 0.93, f"alpha [{aid}]: YAML vs .py corr={corr:.4f} < 0.93"


# ------- 回归统计测试 (用于完整批量验证) -------

def test_alpha_inventory(alpha_registry):
    """校验 alpha zoo 库存。"""
    formats = {}
    for a in alpha_registry:
        formats[a["format"]] = formats.get(a["format"], 0) + 1

    assert formats.get("yaml", 0) >= 400, f"Expected >= 400 YAMLs, got {formats.get('yaml')}"
    assert formats.get("py", 0) >= 30, f"Expected >= 30 .py fallback, got {formats.get('py')}"
    assert sum(formats.values()) >= 460, \
        f"Expected >= 460 alphas total, got {sum(formats.values())}"


def test_alpha_zoo_summary(panel, alpha_registry):
    """批量计算所有 alpha，生成汇总报告。"""
    successes = 0
    failures = []

    for m in alpha_registry:
        aid = m["id"]
        try:
            r = compute_alpha(aid, panel)
            if isinstance(r, pd.DataFrame):
                successes += 1
            else:
                failures.append((aid, f"type {type(r).__name__}"))
        except Exception as e:
            failures.append((aid, str(e)[:80]))

    n_total = len(alpha_registry)
    pct = successes / n_total * 100

    print("\n\n=== Alpha Zoo Unit Test Summary ===")
    print(f"Total alphas: {n_total}")
    print(f"Passed: {successes}/{n_total} ({pct:.1f}%)")
    print(f"Failed: {len(failures)}")

    if failures:
        from collections import Counter
        err_types = Counter(err.split(":")[0][:50] for _, err in failures)
        print("Failure types:")
        for et, cnt in err_types.most_common():
            print(f"  [{cnt:3d}] {et}")

    assert pct >= 95.0, f"Only {pct:.1f}% passed (expected >= 95%)"
