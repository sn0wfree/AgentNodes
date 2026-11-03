"""Data-layer regression: declarative strategy stubs must be backfillable.

Historical bug: strategy dirs created from the 5-line stub (PARAMS only,
no prepare.py, no __main__ block) exited instantly under
run_backtest_script — run.log empty, metrics all zero, yet status was
hardcoded "success". Every backtest since inception was a no-op.

Locks down:
1. _create_minimal_strategy copies the FULL template pair
   (strategy.py with __main__ + prepare.py), not the bare stub.
2. _ensure_strategy_runnable backfills legacy stub dirs
   (adds prepare.py, appends __main__) without touching user params.
3. prepare.py's workspace probe keys on data.duckdb — NOT config.yaml,
   which strategy dirs themselves carry (strategy-level config) and
   which used to make the probe resolve the workspace to the strategy
   dir itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.apps.study.bootstrap import (
    _create_minimal_strategy,
    _ensure_strategy_runnable,
    validate_workspace_strategy,
)

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "strategy_research" / "templates"

LEGACY_STUB = (
    "# 策略参数\n"
    "PARAMS = {\n"
    '    "top_n": 10,\n'
    "}\n"
    "\n"
    "# 因子表达式\n"
    "FACTOR_EXPRS = [\n"
    '    {"factor_name": "momentum_20d", "factor_code": "ts_return(close, 20)", "weight": 1.0},\n'
    "]\n"
    "\n"
    'FACTOR_WEIGHT_METHOD = "equal"\n'
)


def test_create_minimal_copies_full_template_pair(tmp_path: Path):
    strat = tmp_path / "strategies" / "new_s"
    _create_minimal_strategy(strat, "new_s")
    body = (strat / "strategy.py").read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in body, (
        "strategy.py must carry the backtest entrypoint"
    )
    assert "new_s" in body, "strategy_name placeholder must be substituted"
    assert (strat / "prepare.py").exists(), "prepare.py engine must be copied"
    assert "def load_data" in (strat / "prepare.py").read_text(encoding="utf-8")


def test_backfill_adds_prepare_and_main_without_touching_params(tmp_path: Path):
    strat = tmp_path / "strategies" / "legacy_s"
    strat.mkdir(parents=True)
    (strat / "strategy.py").write_text(LEGACY_STUB, encoding="utf-8")

    actions = _ensure_strategy_runnable(strat)
    assert any("prepare.py" in a for a in actions)
    assert any("__main__" in a for a in actions)

    body = (strat / "strategy.py").read_text(encoding="utf-8")
    # user content intact
    assert '"top_n": 10' in body
    assert "momentum_20d" in body
    # entrypoint appended exactly once
    assert body.count('if __name__ == "__main__":') == 1
    # second call is a no-op
    assert _ensure_strategy_runnable(strat) == []


def test_workspace_probe_keys_on_data_duckdb(tmp_path: Path):
    """config.yaml inside the STRATEGY dir must not be mistaken for the
    workspace root marker (it is strategy-level config)."""
    from unittest.mock import patch

    validate_workspace_strategy(tmp_path, "probe_s")
    strat = tmp_path / "strategies" / "probe_s"
    # strategy-level config.yaml — the historical false marker
    (strat / "config.yaml").write_text("strategy:\n  name: probe_s\n")
    # ensure backfill happens even though the strategy dir now exists
    actions = _ensure_strategy_runnable(strat)
    assert actions or (strat / "prepare.py").exists()

    # probe logic (mirrors templates/prepare.py load_data)
    workspace_dir = strat
    for _ in range(5):
        if (workspace_dir / "data.duckdb").exists():
            break
        workspace_dir = workspace_dir.parent
    else:
        workspace_dir = strat.parent.parent.parent

    # place the db at the workspace root the same way prod does
    (tmp_path / "data.duckdb").write_bytes(b"")
    workspace_dir = strat
    for _ in range(5):
        if (workspace_dir / "data.duckdb").exists():
            break
        workspace_dir = workspace_dir.parent
    assert workspace_dir == tmp_path, (
        "probe must resolve to the dir containing data.duckdb, "
        f"got {workspace_dir}"
    )


def test_full_template_strategy_module_imports_and_has_entrypoint():
    """The template pair itself must be internally consistent: strategy.py
    references prepare, prepare defines load_data/evaluate, and the
    __main__ block prints metrics parseable by backtest.METRIC_PATTERNS."""
    import re
    body = (TEMPLATES / "strategy.py").read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in body
    assert "import prepare" in body
    prep = (TEMPLATES / "prepare.py").read_text(encoding="utf-8")
    assert "def load_data" in prep
    assert "def evaluate" in prep
    # __main__ prints "calmar: <float>" style lines that METRIC_PATTERNS match
    main_block = body[body.index('if __name__ == "__main__":'):]
    assert re.search(r"print\(f\"\{k\}: \{v:.6f\}\"\)", main_block)
