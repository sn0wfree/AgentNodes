"""Trust Layer Run Card 生成器 — 借鉴自 vibe-trading backtest/run_card.py.

每次回测运行后,生成 run_card.{json,md} 到 run_dir.

包含:
- schema_version / generated_at
- config summary (codes/start_date/end_date/interval/engine/initial_cash/source)
- config_hash (SHA-256) — 防 config 改动未审计
- strategy_hash (SHA-256 of strategy.py / config.yaml) — 防代码改动未审计
- data_provenance — 数据来源溯源 (loader, fetched_at, cache_key, rows)
- artifact_refs — 输出产物引用 (文件名, SHA-256, 大小)
- 标量 metrics (排除嵌套 dict)
- warnings (异常标签等)
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

SCHEMA_VERSION = "0.3"
BACKTEST_SUMMARY_KEYS = (
    # 回测元数据
    "run",
    "strategy",
    "action",
    # 数据范围
    "codes",
    "start_date",
    "end_date",
    "interval",
    # 回测引擎
    "engine",
    "initial_cash",
    "source",
)


def _config_summary(config: Mapping[str, Any]) -> dict[str, Any]:
    """仅保留 summary 字段,避免泄漏 secrets."""
    return {k: config.get(k) for k in BACKTEST_SUMMARY_KEYS if k in config}


def _hash_dict(obj: Mapping[str, Any]) -> str:
    """SHA-256 of sorted JSON for reproducibility."""
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _hash_file(path: Optional[Path]) -> str:
    """SHA-256 of file content. Returns '' if path is None or missing."""
    if path is None:
        return ""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _collect_artifact_refs(run_dir: Path) -> list[dict[str, Any]]:
    """Collect output artifact references with SHA-256 hashes and sizes."""
    refs: list[dict[str, Any]] = []
    for f in sorted(run_dir.iterdir()):
        if not f.is_file():
            continue
        # Skip run_card itself and large binary files
        if f.name.startswith("run_card"):
            continue
        try:
            stat = f.stat()
            refs.append({
                "filename": f.name,
                "sha256": _hash_file(f),
                "size_bytes": stat.st_size,
            })
        except OSError:
            continue
    return refs


def write_run_card(
    run_dir: Path,
    config: Mapping[str, Any],
    metrics: Mapping[str, Any],
    *,
    strategy_paths: Optional[Sequence[Path]] = None,
    warnings: Optional[Sequence[str]] = None,
    data_source: str = "",
    fetched_at: str = "",
    data_rows: int = 0,
    cache_key: str = "",
) -> dict[str, Any]:
    """写 run_card.json + run_card.md 到 run_dir.

    Args:
        run_dir: run 目录 (如 runs/run_0023/).
        config: 回测配置 (含 codes/start_date/end_date/...).
        metrics: 指标 dict. 仅标量写入 (list/dict 跳过).
        strategy_paths: 策略相关文件路径列表, e.g. [Path("strategy.py"), Path("config.yaml")].
            所有存在的文件都会计算 SHA-256.
        warnings: 异常 / 告警标签 list.
        data_source: 数据来源名称 (如 "tushare", "akshare").
        fetched_at: 数据获取时间 (ISO 格式).
        data_rows: 获取的数据行数.
        cache_key: 缓存键 (如 SHA-256 hash).

    Returns:
        写入 run_card.json 的 payload dict (供测试验证).
    """
    run_dir = Path(run_dir)

    config_summary = _config_summary(config)

    # 收集所有 strategy 文件 hash
    strategy_hashes: dict[str, str] = {}
    if strategy_paths:
        for sp in strategy_paths:
            sp = Path(sp)
            if sp.exists() and sp.is_file():
                strategy_hashes[sp.name] = _hash_file(sp)

    # Data provenance
    data_provenance: dict[str, Any] = {}
    if data_source:
        data_provenance["source"] = data_source
    if fetched_at:
        data_provenance["fetched_at"] = fetched_at
    if data_rows > 0:
        data_provenance["rows"] = data_rows
    if cache_key:
        data_provenance["cache_key"] = cache_key

    # Artifact references
    artifact_refs = _collect_artifact_refs(run_dir)

    run_card = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_dir": run_dir.name,
        "config": config_summary,
        "config_hash": _hash_dict(config_summary),
        "strategy_hashes": strategy_hashes,
        "data_provenance": data_provenance or None,
        "artifact_refs": artifact_refs or None,
        "metrics": {k: v for k, v in metrics.items() if not isinstance(v, (dict, list))},
        "warnings": list(warnings or []),
    }

    # JSON
    json_path = run_dir / "run_card.json"
    json_path.write_text(
        json.dumps(run_card, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Markdown (人读)
    md_path = run_dir / "run_card.md"
    md_lines = _render_markdown(run_card, strategy_hashes, config_summary, data_provenance, artifact_refs, warnings)
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return run_card


def _render_markdown(
    run_card: dict[str, Any],
    strategy_hashes: dict[str, str],
    config_summary: dict[str, Any],
    data_provenance: dict[str, Any],
    artifact_refs: list[dict[str, Any]],
    warnings: Optional[Sequence[str]],
) -> list[str]:
    """Render the human-readable run_card.md lines."""
    md_lines = [
        f"# Run Card `{run_card['run_dir']}`",
        "",
        f"- Schema: `{run_card['schema_version']}`",
        f"- Generated: `{run_card['generated_at']}`",
        f"- Config hash: `{run_card['config_hash'][:16]}...`",
    ]
    if strategy_hashes:
        md_lines.append("- Strategy hashes:")
        for name, h in strategy_hashes.items():
            md_lines.append(f"  - `{name}`: `{h[:16]}...`")
    else:
        md_lines.append("- Strategy hashes: (none)")

    # Data provenance
    if data_provenance:
        md_lines += ["", "## Data Provenance", ""]
        for k, v in data_provenance.items():
            md_lines.append(f"- `{k}`: `{v}`")

    md_lines += [
        "",
        "## Config",
        "",
        "| Key | Value |",
        "|-----|-------|",
    ]
    if config_summary:
        for k, v in config_summary.items():
            md_lines.append(f"| `{k}` | `{v}` |")
    else:
        md_lines.append("| _empty_ | _none_ |")

    md_lines += [
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "|--------|-------|",
    ]
    if run_card["metrics"]:
        for k, v in run_card["metrics"].items():
            if isinstance(v, float):
                md_lines.append(f"| `{k}` | `{v:.4f}` |")
            else:
                md_lines.append(f"| `{k}` | `{v}` |")
    else:
        md_lines.append("| _empty_ | _none_ |")

    # Artifact refs
    if artifact_refs:
        md_lines += [
            "",
            "## Artifacts",
            "",
            "| File | Size | SHA-256 |",
            "|------|------|---------|",
        ]
        for ref in artifact_refs:
            size_kb = ref["size_bytes"] / 1024
            md_lines.append(f"| `{ref['filename']}` | {size_kb:.1f} KB | `{ref['sha256'][:16]}...` |")

    if warnings:
        md_lines += ["", "## Warnings", ""]
        for w in warnings:
            md_lines.append(f"- {w}")

    return md_lines
