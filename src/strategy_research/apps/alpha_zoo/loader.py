"""Alpha Zoo unified loader — single source of truth for alpha computation.

This module consolidates the previously duplicated loading logic that lived in
both ``core/alpha_zoo/__init__.py`` (``compute_alpha``) and
``apps/alpha_zoo_adapter.py`` (``AlphaZooAdapter.compute_as_wide``).

Loading order (priority high → low):
    1. YAML config in ``core/alpha_zoo/<zoo>/<alpha>.yaml``
    2. Python module in ``core/alpha_zoo/<zoo>/<alpha>.py`` (fallback)

Output validation is identical for both paths and runs through
``_validate_result`` below.

Public API
----------
    compute_alpha(alpha_id, panel) -> DataFrame
        The flat function previously in ``alpha_zoo/__init__.py``.
    AlphaLoader
        The class previously in ``alpha_zoo_adapter.py`` (now a thin wrapper
        that delegates to ``compute_alpha``).
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .alpha_zoo_ops import ALPHA_ZOO_OPS  # noqa: F401  (kept for backward re-export)

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────

ALPHA_ZOOS: dict[str, str] = {
    "alpha101": "Kakushadze 101 公式化因子",
    "gtja191": "国泰君安 191 A 股截面因子",
    "qlib158": "微软 Qlib 158 ML 因子",
    "academic": "11 个学术因子",
    "fundamental": "4 个基本面因子",
}

_ZOO_ROOT = Path(__file__).parent


# ── Public: alpha_id parsing ────────────────────────────────────────


def _resolve_alpha_name(zoo_name: str, alpha_id: str) -> str:
    """Strip ``<zoo>_`` prefix to get the local alpha name.

    >>> _resolve_alpha_name("alpha101", "alpha101_001")
    '001'
    >>> _resolve_alpha_name("alpha101", "001")
    '001'
    """
    prefix = f"{zoo_name}_"
    if alpha_id.startswith(prefix):
        return alpha_id[len(prefix):]
    return alpha_id


def parse_alpha_id(alpha_id: str) -> tuple[str, str]:
    """Parse ``alpha_id`` of the form ``<zoo>_<name>`` into ``(zoo, name)``.

    Handles multi-segment zoo prefixes (e.g. ``alpha101_001`` → ``alpha101``).
    Raises ``ValueError`` if the id cannot be matched against a known zoo.
    """
    if "_" not in alpha_id:
        raise ValueError(f"Invalid alpha_id: {alpha_id!r}")
    for zoo_name in ALPHA_ZOOS:
        if alpha_id.startswith(zoo_name + "_"):
            return zoo_name, _resolve_alpha_name(zoo_name, alpha_id)
    # Fallback: first underscore split
    parts = alpha_id.split("_", 1)
    if parts[0] in ALPHA_ZOOS:
        return parts[0], parts[1]
    raise ValueError(
        f"Unknown zoo in alpha_id {alpha_id!r}. Known zoos: {list(ALPHA_ZOOS)}"
    )


# ── Public: file discovery ──────────────────────────────────────────


def _find_yaml_file(zoo_name: str, alpha_name: str) -> Path | None:
    zoo_dir = _ZOO_ROOT / zoo_name
    for cand in (f"{alpha_name}.yaml", f"alpha_{alpha_name}.yaml",
                 f"{zoo_name}_{alpha_name}.yaml"):
        path = zoo_dir / cand
        if path.exists():
            return path
    return None


def _find_py_file(zoo_name: str, alpha_name: str) -> Path | None:
    zoo_dir = _ZOO_ROOT / zoo_name
    for cand in (f"{alpha_name}.py", f"alpha_{alpha_name}.py",
                 f"{zoo_name}_{alpha_name}.py"):
        path = zoo_dir / cand
        if path.exists():
            return path
    return None


def _resolve_alpha_module_path(zoo_name: str, alpha_name: str, py_file: Path) -> str:
    """Build the dotted module path for a .py alpha file."""
    # alpha_name may carry a "alpha_" prefix already; module path uses the file stem.
    module_name = py_file.stem
    return f"strategy_research.apps.alpha_zoo.{zoo_name}.{module_name}"


# ── Public: validation (used by both YAML and py paths) ─────────────


def _validate_result(
    result: Any,
    alpha_id: str,
    expected_shape: tuple[int, int],
    *,
    source: str,
    max_inf_ratio: float = 0.30,
    max_nan_ratio: float = 0.98,
) -> None:
    """Validate the result of an alpha computation.

    Raises:
        TypeError: result is not a DataFrame
        ValueError: shape mismatch / inf > threshold
    Warns:
        logger: NaN ratio > threshold (high warmup NaN is allowed, just noisy)
    """
    if not isinstance(result, pd.DataFrame):
        raise TypeError(
            f"Alpha {alpha_id} ({source}) must return DataFrame, got {type(result).__name__}"
        )
    if result.shape != expected_shape:
        raise ValueError(
            f"Shape mismatch for {alpha_id} ({source}): {result.shape} != {expected_shape}"
        )
    n_inf = int(np.isinf(result.values).sum())
    total = result.size
    if total > 0 and n_inf / total > max_inf_ratio:
        raise ValueError(
            f"Alpha {alpha_id} ({source}): {n_inf}/{total} inf values "
            f"({n_inf / total:.1%}) exceeds {max_inf_ratio:.0%}"
        )
    n_nan = int(np.isnan(result.values).sum())
    if total > 0 and n_nan / total > max_nan_ratio:
        logger.warning(
            "Alpha %s (%s): %d/%d NaN values (%.1f%%), mostly empty",
            alpha_id, source, n_nan, total, n_nan / total * 100,
        )


# ── Public: compute (YAML + py fallback) ────────────────────────────


def compute_alpha(alpha_id: str, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Compute a single alpha by ``alpha_id``.

    Loading order:
        1. YAML (preferred, deterministic)
        2. .py (fallback if YAML missing or fails)

    Raises:
        ValueError: invalid alpha_id or shape mismatch
        FileNotFoundError: neither YAML nor .py found
        ImportError: .py file exists but cannot be imported
        AttributeError: .py module has no ``compute()`` function
    """
    zoo_name, alpha_name = parse_alpha_id(alpha_id)
    expected_shape = panel["close"].shape

    # 1) YAML
    yaml_file = _find_yaml_file(zoo_name, alpha_name)
    if yaml_file:
        try:
            from .alpha_zoo_yaml import compute_alpha_from_yaml, load_alpha_yaml
            config = load_alpha_yaml(yaml_file)
            result = compute_alpha_from_yaml(config, panel)
            _validate_result(result, alpha_id, expected_shape, source="yaml")
            return result
        except Exception as exc:
            logger.debug(
                "YAML load failed for %s, trying .py fallback: %s",
                alpha_id, exc,
            )

    # 2) .py fallback
    py_file = _find_py_file(zoo_name, alpha_name)
    if py_file:
        module_path = _resolve_alpha_module_path(zoo_name, alpha_name, py_file)
        try:
            mod = importlib.import_module(module_path)
        except ImportError as exc:
            raise ImportError(f"Cannot load alpha {alpha_id}: {exc}") from exc

        if not hasattr(mod, "compute"):
            raise AttributeError(f"Alpha {alpha_id} has no compute() function")

        result = mod.compute(panel)
        _validate_result(result, alpha_id, expected_shape, source="py")
        return result

    raise FileNotFoundError(
        f"Alpha {alpha_id} not found (no .yaml or .py in {zoo_name}/)"
    )


# ── Public: list_alphas ─────────────────────────────────────────────


def list_alphas(zoo: str | None = None) -> list[dict[str, str]]:
    """List all available alphas across zoos (or just one zoo).

    Returns a deduplicated list of dicts with ``id``, ``zoo``, ``file``,
    ``format`` keys.
    """
    results: list[dict[str, str]] = []
    seen: set[str] = set()

    for zoo_name in ALPHA_ZOOS:
        if zoo and zoo_name != zoo:
            continue
        zoo_dir = _ZOO_ROOT / zoo_name

        for f in sorted(zoo_dir.glob("*.yaml")):
            alpha_id = f"{zoo_name}_{f.stem}"
            if alpha_id in seen:
                continue
            results.append({
                "id": alpha_id,
                "zoo": zoo_name,
                "file": str(f),
                "format": "yaml",
            })
            seen.add(alpha_id)

        for f in sorted(zoo_dir.glob("*.py")):
            if f.name.startswith("_"):
                continue
            alpha_id = f"{zoo_name}_{f.stem}"
            if alpha_id in seen:
                continue
            results.append({
                "id": alpha_id,
                "zoo": zoo_name,
                "file": str(f),
                "format": "py",
            })
            seen.add(alpha_id)

    return results


# ── Public: meta extraction (AST-based, no import) ──────────────────


def _load_meta_from_py(py_file: Path) -> dict[str, Any] | None:
    """Extract ``__alpha_meta__`` dict from a .py alpha file via AST.

    Avoids the cost (and side-effects) of ``importlib.import_module``.
    """
    import ast
    try:
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception:
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Name)
                    and target.id == "__alpha_meta__"
                    and isinstance(node.value, ast.Dict)):
                meta: dict[str, Any] = {}
                for key, value in zip(node.value.keys, node.value.values):
                    if not isinstance(key, ast.Constant):
                        continue
                    k = key.value
                    if isinstance(value, ast.Constant):
                        meta[k] = value.value
                    elif isinstance(value, ast.List):
                        meta[k] = [
                            e.value for e in value.elts
                            if isinstance(e, ast.Constant)
                        ]
                return meta
    return None


# ── Public: thin class wrapper (preserves the previous API) ────────


class AlphaLoader:
    """High-level alpha loader.

    Thin wrapper over the module-level functions ``compute_alpha``,
    ``list_alphas`` and ``_load_meta_from_py``. Kept for backward
    compatibility with the previous ``AlphaZooAdapter`` API; new code
    should call ``compute_alpha`` / ``list_alphas`` directly.
    """

    def __init__(self, zoo_root: Path | None = None) -> None:
        # ``zoo_root`` retained for API compat; the module-level
        # ``_ZOO_ROOT`` is the authoritative path.
        self._zoo_root_override = zoo_root

    # ── Listings ──────────────────────────────────────────────────

    def list_alphas(
        self,
        zoo: str | None = None,
        theme: str | None = None,
        universe: str | None = None,
    ) -> list[dict[str, Any]]:
        """List available alphas, optionally filtered by ``zoo``/``theme``/``universe``.

        ``theme`` and ``universe`` filters require reading each .py file's
        ``__alpha_meta__`` block (AST parse, no import).
        """
        results: list[dict[str, Any]] = []
        for zoo_name in ALPHA_ZOOS:
            if zoo and zoo_name != zoo:
                continue
            zoo_dir = (_ZOO_ROOT if self._zoo_root_override is None
                       else self._zoo_root_override) / zoo_name
            for f in sorted(zoo_dir.glob("*.py")):
                if f.name.startswith("_") or f.name == "__init__.py":
                    continue
                alpha_id = f"{zoo_name}_{f.stem}"
                meta = _load_meta_from_py(f)
                if not meta:
                    results.append({
                        "id": alpha_id,
                        "zoo": zoo_name,
                        "meta": {},
                    })
                    continue
                if theme and theme not in meta.get("theme", []):
                    continue
                if universe and universe not in meta.get("universe", []):
                    continue
                results.append({
                    "id": alpha_id,
                    "zoo": zoo_name,
                    "meta": meta,
                })
        return results

    def get_alpha(self, alpha_id: str) -> dict[str, Any]:
        """Return metadata for a single alpha id.

        Raises ``KeyError`` for invalid ids or missing alphas.
        """
        try:
            zoo_name, alpha_name = parse_alpha_id(alpha_id)
        except ValueError as exc:
            raise KeyError(str(exc)) from exc

        zoo_dir = (_ZOO_ROOT if self._zoo_root_override is None
                   else self._zoo_root_override) / zoo_name
        py_file = zoo_dir / f"{alpha_name}.py"
        if not py_file.exists():
            raise KeyError(f"Alpha not found: {alpha_id}")
        meta = _load_meta_from_py(py_file) or {}
        return {"id": alpha_id, "zoo": zoo_name, "meta": meta, "file": str(py_file)}

    # ── Compute ───────────────────────────────────────────────────

    def _ensure_panel(
        self,
        prices: pd.DataFrame,
        volume: pd.DataFrame | None = None,
        open_: pd.DataFrame | None = None,
        high: pd.DataFrame | None = None,
        low: pd.DataFrame | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Build a panel dict from optional OHLCV inputs, auto-filling derived cols."""
        panel: dict[str, pd.DataFrame] = {"close": prices}
        if volume is not None:
            panel["volume"] = volume
        if open_ is not None:
            panel["open"] = open_
        if high is not None:
            panel["high"] = high
        if low is not None:
            panel["low"] = low

        # Fill missing OHLCV
        for col in ("open", "high", "low"):
            if col not in panel:
                panel[col] = prices
        if "volume" not in panel:
            panel["volume"] = pd.DataFrame(
                0, index=prices.index, columns=prices.columns
            )

        # Derived columns
        if "vwap" not in panel:
            panel["vwap"] = (panel["high"] + panel["low"] + panel["close"]) / 3.0
        if "amount" not in panel:
            panel["amount"] = panel["volume"] * panel["close"]
        if "returns" not in panel:
            panel["returns"] = panel["close"].pct_change().fillna(0)
        for w in (5, 10, 15, 20, 30, 50, 60):
            key = f"adv{w}"
            if key not in panel:
                panel[key] = panel["volume"].rolling(w).mean().fillna(
                    panel["volume"].mean()
                )
        return panel

    def compute_as_wide(
        self,
        alpha_id: str,
        prices: pd.DataFrame,
        volume: pd.DataFrame | None = None,
        open_: pd.DataFrame | None = None,
        high: pd.DataFrame | None = None,
        low: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Compute an alpha and return a wide DataFrame (index=date, columns=assets).

        Equivalent to ``compute_alpha(alpha_id, self._ensure_panel(...))``.
        """
        panel = self._ensure_panel(prices, volume, open_, high, low)
        return compute_alpha(alpha_id, panel)

    def compute_as_series(
        self,
        alpha_id: str,
        prices: pd.DataFrame,
        **kwargs: Any,
    ) -> pd.Series:
        """Compute an alpha and return a stacked Series (MultiIndex date+asset)."""
        wide = self.compute_as_wide(alpha_id, prices, **kwargs)
        return wide.stack()

    def compute_batch(
        self,
        alpha_ids: list[str],
        prices: pd.DataFrame,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """Compute many alphas; failures are logged and skipped."""
        results: dict[str, pd.Series] = {}
        for aid in alpha_ids:
            try:
                results[aid] = self.compute_as_series(aid, prices, **kwargs)
            except Exception as exc:
                logger.warning(f"⚠️  因子 {aid} 计算失败: {exc}")
        return pd.DataFrame(results)

    def health(self) -> dict[str, Any]:
        """Quick health check: count alphas with parseable meta vs not."""
        loaded = 0
        failed = 0
        errors: list[str] = []
        for zoo_name in ALPHA_ZOOS:
            zoo_dir = (_ZOO_ROOT if self._zoo_root_override is None
                       else self._zoo_root_override) / zoo_name
            for f in sorted(zoo_dir.glob("*.py")):
                if f.name.startswith("_") or f.name == "__init__.py":
                    continue
                alpha_id = f"{zoo_name}_{f.stem}"
                meta = _load_meta_from_py(f)
                if meta:
                    loaded += 1
                else:
                    failed += 1
                    errors.append(f"{alpha_id}: no __alpha_meta__")
        return {"loaded": loaded, "failed": failed, "errors": errors[:20]}

    # ── Backward-compat private aliases ─────────────────────────
    #
    # ``AlphaZooAdapter._parse_id`` and ``AlphaZooAdapter._load_meta`` were
    # referenced by ``tests/test_alpha_zoo_adapter.py``. Keep them as thin
    # shims so that test surface is unchanged.

    def _parse_id(self, alpha_id: str) -> tuple[str, str]:
        """Backward-compat alias for :func:`parse_alpha_id`.

        Translates ``ValueError`` (raised on unparseable ids) to ``KeyError``
        for compatibility with the original ``AlphaZooAdapter`` API.
        """
        try:
            return parse_alpha_id(alpha_id)
        except ValueError as exc:
            raise KeyError(str(exc)) from exc

    def _load_meta(self, py_file: Path) -> dict[str, Any] | None:
        """Backward-compat alias for :func:`_load_meta_from_py`."""
        return _load_meta_from_py(py_file)


__all__ = [
    "ALPHA_ZOOS",
    "AlphaLoader",
    "compute_alpha",
    "list_alphas",
    "parse_alpha_id",
]
