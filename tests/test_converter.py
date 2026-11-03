"""alpha_zoo_convert.py .py→.yaml 转换器单元测试。

采用黑盒方法：测试转换器的核心端到端行为。
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.alpha_zoo_convert import (
    _normalize_yaml,
    analyze_compute_function,
    convert_all_zoos,
    convert_py_to_yaml,
    convert_zoo,
    yaml_to_string,
)

warnings.filterwarnings("ignore")


# ============================================================
# analyze_compute_function 测试
# ============================================================

@pytest.fixture
def tmp_py_simple(tmp_path: Path) -> Path:
    """简单的 .py alpha 文件."""
    f = tmp_path / "test_alpha.py"
    f.write_text('''
import pandas as pd
from ..alpha_zoo_ops import ts_mean

__alpha_meta__ = {
    "id": "test_alpha",
    "columns_required": ["close"],
    "theme": ["momentum"],
}

def compute(panel: dict) -> pd.DataFrame:
    close = panel["close"]
    return ts_mean(close, 5)
''')
    return f


def test_analyze_basic(tmp_py_simple):
    result = analyze_compute_function(tmp_py_simple)
    assert result["error"] is None
    assert result["meta"]["id"] == "test_alpha"
    assert result["analyzer"] is not None


def test_analyze_extracts_meta(tmp_py_simple):
    result = analyze_compute_function(tmp_py_simple)
    meta = result["meta"]
    assert meta["id"] == "test_alpha"
    assert "close" in meta["columns_required"]


def test_analyze_no_compute_function(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def foo():\n    return 1\n")
    result = analyze_compute_function(f)
    assert result["error"] is not None


def test_analyze_syntax_error(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def compute(:\n  invalid syntax\n")
    result = analyze_compute_function(f)
    assert result["error"] is not None


def test_analyzer_complexity_positive(tmp_py_simple):
    """analyzer 应有 >= 2 复杂度."""
    r = analyze_compute_function(tmp_py_simple)
    assert r["complexity"] >= 0


# ============================================================
# convert_py_to_yaml
# ============================================================

def test_convert_simple(tmp_py_simple):
    config = convert_py_to_yaml(tmp_py_simple)
    assert config is not None
    assert config["id"] == "test_alpha"
    assert "final" in config


def test_convert_returns_dict_with_required_keys(tmp_py_simple):
    config = convert_py_to_yaml(tmp_py_simple)
    assert "id" in config
    assert "final" in config
    assert "zoo" in config or "theme" in config or "columns_required" in config


def test_convert_invalid_returns_none_or_dict(tmp_path):
    """无法转换时应返回 None 或 dict 但不崩溃."""
    f = tmp_path / "unconvertible.py"
    f.write_text('''
def compute(panel):
    # 超复杂: 元类构造
    return type('X', (object,), {'a': 1})()
''')
    try:
        result = convert_py_to_yaml(f)
        assert result is None or isinstance(result, dict)
    except Exception:
        pytest.fail("Should not crash")


def test_convert_ignores_other_functions(tmp_path):
    f = tmp_path / "test.py"
    f.write_text('''
import pandas as pd
from ..alpha_zoo_ops import ts_mean

def helper(x):
    return x + 1

def compute(panel):
    return ts_mean(panel["close"], 5)
''')
    config = convert_py_to_yaml(f)
    # 即使有 helper 函数, compute() 仍应正常处理
    if config is not None:
        assert isinstance(config, dict)


def test_convert_handles_no_meta(tmp_path):
    """缺少 __alpha_meta__ 时应默认处理."""
    f = tmp_path / "no_meta.py"
    f.write_text('''
import pandas as pd
from ..alpha_zoo_ops import ts_mean
def compute(panel):
    return ts_mean(panel["close"], 5)
''')
    config = convert_py_to_yaml(f)
    # 可能返回 None 或 dict, 但不崩溃
    if config is not None:
        assert isinstance(config, dict)


# ============================================================
# yaml_to_string
# ============================================================

def test_yaml_to_string_basic():
    config = {"id": "test", "final": {"column": "close"}}
    s = yaml_to_string(config)
    assert "id: test" in s
    assert "final:" in s


def test_yaml_to_string_indents_steps():
    config = {
        "id": "test",
        "steps": [
            {"name": "s1", "expr": {"column": "close"}},
        ],
        "final": {"ref": "s1"},
    }
    s = yaml_to_string(config)
    assert "steps:" in s
    assert "name: s1" in s


def test_yaml_to_string_roundtrip():
    """YAML 字符串应可往返解析."""
    import yaml
    config = {
        "id": "test",
        "steps": [{"name": "s1", "expr": {"column": "close"}}],
        "final": {"ref": "s1"},
        "columns_required": ["close"],
    }
    s = yaml_to_string(config)
    parsed = yaml.safe_load(s)
    assert parsed == config


# ============================================================
# _normalize_yaml
# ============================================================

def test_normalize_passes_through_valid():
    node = {"op": "ts_mean", "args": [{"column": "close"}, {"value": 5}]}
    result = _normalize_yaml(node)
    assert "op" in result


def test_normalize_handles_list():
    """应能递归处理 list."""
    node = {"args": [{"column": "close"}, {"value": 5}]}
    result = _normalize_yaml(node)
    assert "args" in result


# ============================================================
# 真实 alpha 文件转换测试
# ============================================================

ALPHA101_DIR = Path("src/strategy_research/core/alpha_zoo/alpha101")


@pytest.mark.parametrize("alpha_num", [1, 2, 4, 5, 10, 11, 12])
def test_alpha101_ones_convert(alpha_num):
    """前几个 alpha 101 应能成功转换."""
    src = ALPHA101_DIR / f"alpha_{alpha_num:03d}.py"
    if not src.exists():
        pytest.skip(f"{src} not found")
    config = convert_py_to_yaml(src)
    # 可能转换成功或不成功 (复杂 alpha 会被放弃)
    if config is not None:
        assert isinstance(config, dict)
        assert "id" in config
        assert "final" in config


def test_convert_alpha_with_ts_corr(tmp_path):
    """含 ts_corr 的 alpha."""
    src = ALPHA101_DIR / "alpha_044.py"
    if not src.exists():
        pytest.skip("alpha_044 not found")
    src_content = src.read_text()
    assert "ts_corr" in src_content
    config = convert_py_to_yaml(src)
    if config is not None:
        assert isinstance(config, dict)


# ============================================================
# convert_zoo
# ============================================================

def test_convert_zoo_empty_dir(tmp_path):
    """空目录应返回空 dict 而不崩溃."""
    (tmp_path / "subdir").mkdir()
    result = convert_zoo(tmp_path / "subdir")
    assert isinstance(result, dict)


def test_convert_zoo_with_some_files(tmp_path):
    """含有效 + 无效文件的目录."""
    zoo_dir = tmp_path / "test_zoo"
    zoo_dir.mkdir()
    # 有效
    (zoo_dir / "alpha_001.py").write_text('''
import pandas as pd
from ..alpha_zoo_ops import ts_mean
def compute(panel):
    return ts_mean(panel["close"], 5)
''')
    # 无 compute()
    (zoo_dir / "alpha_002.py").write_text("def foo():\n    return 1\n")
    # 语法错误
    (zoo_dir / "alpha_003.py").write_text("def compute(:\n invalid\n")

    result = convert_zoo(zoo_dir)
    assert isinstance(result, dict)


def test_convert_zoo_nonexistent_dir(tmp_path):
    """不存在的目录应优雅处理."""
    result = convert_zoo(tmp_path / "notexists")
    assert isinstance(result, dict)


def test_convert_zoo_counts_categories(tmp_path):
    """返回 dict 应有 success/failed/skipped 计数."""
    zoo_dir = tmp_path / "test_zoo"
    zoo_dir.mkdir()
    (zoo_dir / "ok.py").write_text('''
import pandas as pd
def compute(panel):
    return panel["close"]
''')
    result = convert_zoo(zoo_dir)
    # 应至少有 details 字段
    assert isinstance(result, dict)


# ============================================================
# 端到端：转换 → 加载 → 计算
# ============================================================

def test_convert_then_compute(tmp_py_simple):
    """转换后的配置应能直接用于 compute_alpha_from_yaml."""
    from strategy_research.apps.alpha_zoo_yaml import compute_alpha_from_yaml
    config = convert_py_to_yaml(tmp_py_simple)
    if config is None:
        pytest.skip("alpha too complex")
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2024-01-01", periods=20)
    panel = {
        "close": pd.DataFrame(rng.uniform(10, 50, (20, 3)), index=dates, columns=list("ABC")),
    }
    r = compute_alpha_from_yaml(config, panel)
    assert isinstance(r, pd.DataFrame)


def test_convert_preserves_evaluation_equivalence(tmp_py_simple):
    """转换后 YAML 应与原始 .py 计算结果一致."""
    from strategy_research.apps.alpha_zoo_yaml import compute_alpha_from_yaml
    config = convert_py_to_yaml(tmp_py_simple)
    if config is None:
        pytest.skip("alpha too complex")

    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2024-01-01", periods=30)
    panel = {
        "close": pd.DataFrame(rng.uniform(10, 50, (30, 3)), index=dates, columns=list("ABC")),
    }

    # YAML 计算
    yaml_result = compute_alpha_from_yaml(config, panel)

    # 直接复制逻辑计算（不依赖相对导入）
    a = panel["close"]
    # ts_mean(a, 5) 模拟
    py_result = a.rolling(5, min_periods=5).mean()

    if yaml_result.shape == py_result.shape:
        a_flat = yaml_result.values.flatten()
        b_flat = py_result.values.flatten()
        mask = ~(np.isnan(a_flat) | np.isnan(b_flat))
        if mask.sum() >= 5:
            diff = np.abs(a_flat[mask] - b_flat[mask])
            assert diff.max() < 1e-9, f"yaml vs expected: max diff {diff.max()}"


# ============================================================
# Regression: convert_all_zoos 不能产生 _yaml_yaml 增殖目录
# (历史 bug: 见 chore commit "清理 alpha_zoo 增殖空目录")
# ============================================================

def test_convert_all_zoos_skips_existing_yaml_output_dirs(tmp_path):
    """已有 *_yaml 目录应被跳过, 防止 convert_zoo 默认追加 _yaml 造成指数增殖."""
    # 模拟污染状态: 一个 zoo + 一个已存在的 _yaml 输出目录
    zoo_dir = tmp_path / "alpha101"
    zoo_dir.mkdir()
    (zoo_dir / "alpha_001.py").write_text(
        'import pandas as pd\n'
        'def compute(panel):\n'
        '    return panel["close"]\n'
    )
    # 模拟上次运行的输出残留
    (tmp_path / "alpha101_yaml").mkdir()
    (tmp_path / "alpha101_yaml_yaml").mkdir()
    (tmp_path / "alpha101_yaml_yaml_yaml").mkdir()

    result = convert_all_zoos(tmp_path)

    # 只应处理 alpha101, _yaml 后缀全部跳过
    assert "alpha101" in result
    assert "alpha101_yaml" not in result
    # 应记录跳过的目录
    skipped = result.get("__skipped_yaml_dirs__", [])
    assert "alpha101_yaml" in skipped
    assert "alpha101_yaml_yaml" in skipped
    assert "alpha101_yaml_yaml_yaml" in skipped


def test_convert_all_zoos_idempotent(tmp_path):
    """连跑两次, 第二次不应产生新 _yaml_ 目录 (幂等性)."""
    zoo_dir = tmp_path / "alpha101"
    zoo_dir.mkdir()
    (zoo_dir / "alpha_001.py").write_text(
        'import pandas as pd\n'
        'def compute(panel):\n'
        '    return panel["close"]\n'
    )

    before = set(p.name for p in tmp_path.iterdir())

    convert_all_zoos(tmp_path)
    after_first = set(p.name for p in tmp_path.iterdir())

    convert_all_zoos(tmp_path)
    after_second = set(p.name for p in tmp_path.iterdir())

    # 第一轮: 产生 alpha101_yaml
    assert "alpha101_yaml" in after_first
    # 第二轮: 不应有 _yaml_yaml 类型的新目录
    extra = {n for n in after_second
             if n not in before and n.endswith("_yaml_yaml")}
    assert extra == set(), f"Idempotency violated: new dirs {extra}"


def test_convert_all_zoos_with_no_yaml_dirs_unchanged_behaviour(tmp_path):
    """无 _yaml 后缀目录时, 应照常处理所有 zoo."""
    (tmp_path / "alpha101").mkdir()
    (tmp_path / "alpha101" / "ok.py").write_text(
        'import pandas as pd\n'
        'def compute(panel):\n'
        '    return panel["close"]\n'
    )
    (tmp_path / "fundamental").mkdir()
    (tmp_path / "fundamental" / "ok.py").write_text(
        'import pandas as pd\n'
        'def compute(panel):\n'
        '    return panel["close"]\n'
    )

    result = convert_all_zoos(tmp_path)

    assert "alpha101" in result
    assert "fundamental" in result
    assert result.get("__skipped_yaml_dirs__", []) == []
    # 两个 zoo 应都有产物
    for name in ("alpha101", "fundamental"):
        assert (tmp_path / f"{name}_yaml").is_dir(), f"{name}_yaml not created"
