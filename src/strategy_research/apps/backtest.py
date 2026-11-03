"""回测执行工具。

运行策略回测，保存结果到 runs/run_XXXX/ 和 DuckDB。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from strategy_research.apps.db import (
    save_backtest_result,
    save_nav_history,
    save_weight_history,
)
from strategy_research.foundation.git import git_commit as git_commit
from strategy_research.foundation.git import git_commit_rich, git_get_hash
from strategy_research.apps.run_card import write_run_card

logger = logging.getLogger(__name__)

# ============================================================
# 子进程环境变量白名单
# ============================================================

# Only these env vars are passed to backtest subprocesses.
# Security boundary: LLM keys, API server, broker, and live-trading
# credentials are explicitly excluded.
_ALLOWED_ENV_KEYS: set[str] = {
    # OS / Python basics
    "PATH", "PYTHONPATH", "HOME", "USER", "LANG", "LC_ALL", "LC_CTYPE",
    "SHELL", "TMPDIR", "TEMP", "TMP",
    # Python internals
    "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "PYTHONIOENCODING",
    # Proxy / certificates (read-only, safe to forward)
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    # Market data (read-only tokens, safe to forward)
    "TUSHARE_TOKEN", "TUSHARE_API_KEY",
    "IFIND_TOKEN", "IFIND_API_KEY",
    "FRED_API_KEY",
    "YAHOO_FINANCE_API_KEY",
    # Encoding
    "PYTHONUTF8",
}


def _build_restricted_env() -> dict[str, str]:
    """Build a restricted environment for subprocess execution.

    Only whitelisted env vars are included. This prevents accidental
    leakage of LLM keys, broker credentials, or API server tokens
    into generated strategy code.
    """
    base = os.environ.copy()
    return {k: v for k, v in base.items() if k in _ALLOWED_ENV_KEYS}


# ============================================================
# 指标解析
# ============================================================

METRIC_PATTERNS = [
    (r"^calmar:\s*([0-9.eE+-]+)$", "calmar", float),
    (r"^sharpe:\s*([0-9.eE+-]+)$", "sharpe", float),
    (r"^max_dd:\s*([0-9.eE+-]+)$", "max_dd", float),
    (r"^ann_return:\s*([0-9.eE+-]+)$", "ann_return", float),
    (r"^ann_vol:\s*([0-9.eE+-]+)$", "ann_vol", float),
    (r"^sortino:\s*([0-9.eE+-]+)$", "sortino", float),
    (r"^turnover:\s*([0-9.eE+-]+)$", "turnover", float),
    (r"^win_rate:\s*([0-9.eE+-]+)$", "win_rate", float),
    (r"^trades:\s*([0-9.eE+-]+)$", "trades", float),
]


def parse_run_log(log_path: Path) -> dict:
    """解析 run.log 提取指标。"""
    metrics = {}
    if not log_path.exists():
        return metrics

    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            for pattern, name, converter in METRIC_PATTERNS:
                match = re.match(pattern, line, re.IGNORECASE)
                if match:
                    try:
                        metrics[name] = converter(match.group(1))
                    except (ValueError, IndexError):
                        pass
                    break

    return metrics


# ============================================================
# 结构化指标契约
# ============================================================
#
# 信任模型:LLM 生成的 strategy.py 可以打印任意数字,自由文本(run.log)
# 不能作为验收指标的唯一来源。契约线 = 框架拥有的 templates/prepare.py
# (Agent 不应修改此文件)在子进程内把 evaluate() 结果写到
# <SR_RUN_DIR>/metrics_raw.json;框架以该文件为第一来源,run.log 正则
# 仅用于旧策略降级 + 同源一致性交叉校验。残余风险(策略代码绕过
# prepare 自写契约文件)由"修改 prepare.py 即违规"的约定与代码审计兜底,
# 与旧纯正则方案相比至少消除了格式漂移和打印即真相两个失效模式。

def _reject_json_constant(name: str) -> float:
    """json parse_constant 钩子:Infinity/NaN 一律拒绝。"""
    raise ValueError(f"non-finite JSON constant: {name}")


def read_metrics_contract(path: Path) -> Optional[dict]:
    """读取策略子进程写出的结构化指标契约文件。

    协议: ``{"protocol": 1, "metrics": {name: number}}``。
    文件不存在(旧策略/手工运行)→ None。协议不符、非数值键、非有限值
    → None 并告警(调用方走 run.log 降级路径,同时留痕)。
    """
    if not path.exists():
        return None
    try:
        data = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
        if not isinstance(data, dict) or data.get("protocol") != 1:
            logger.warning("metrics contract %s: unknown protocol", path)
            return None
        raw = data.get("metrics")
        if not isinstance(raw, dict):
            logger.warning("metrics contract %s: missing metrics dict", path)
            return None
        out: dict[str, float] = {}
        for k, v in raw.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                logger.warning("metrics contract %s: non-numeric key %r", path, k)
                return None
            out[str(k)] = float(v)
        return out
    except (ValueError, OSError) as exc:
        logger.warning("metrics contract %s unreadable: %s", path, exc)
        return None


# run.log 指标打印精度为 %.6f → 相对容差 5e-6(同源数据应精确一致)
_CONTRACT_LOG_TOLERANCE = 5e-6


def _cross_check_contract(contract: dict, scraped: dict) -> list[str]:
    """契约值 vs run.log 打印值交叉校验(两者同源,应一致)。

    Returns:
        不一致项列表(空 = 通过)。
    """
    problems: list[str] = []
    for key, log_v in scraped.items():
        if key not in contract:
            continue
        try:
            cv, lv = float(contract[key]), float(log_v)
        except (TypeError, ValueError):
            continue
        tol = _CONTRACT_LOG_TOLERANCE * max(1.0, abs(cv), abs(lv))
        if abs(cv - lv) > tol:
            problems.append(f"{key}: contract={cv:.6g} run.log={lv:.6g}")
    return problems


# ============================================================
# Run 目录管理
# ============================================================

def get_next_run_name(strategy_dir: Path) -> str:
    """获取下一个 run 名称。"""
    runs_dir = strategy_dir / "runs"
    if not runs_dir.exists():
        return "run_0001"

    existing = []
    for d in runs_dir.iterdir():
        if d.is_dir() and d.name.startswith("run_"):
            try:
                num = int(d.name.split("_")[1])
                existing.append(num)
            except (ValueError, IndexError):
                pass

    if not existing:
        return "run_0001"

    next_num = max(existing) + 1
    return f"run_{next_num:04d}"


def create_run_dir(strategy_dir: Path, run_name: str) -> Path:
    """创建 run 目录。"""
    run_dir = strategy_dir / "runs" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def save_run_snapshot(strategy_dir: Path, run_dir: Path) -> None:
    """保存策略快照。"""
    # Study 场景 strategy_dir == run_dir（v2 每轮独立目录）：跳过自复制
    same_dir = run_dir.resolve() == strategy_dir.resolve()
    src = strategy_dir / "strategy.py"
    dst = run_dir / "strategy.py"
    if src.exists() and not same_dir:
        shutil.copy2(src, dst)

    # 也保存 config.yaml
    config_src = strategy_dir / "config.yaml"
    config_dst = run_dir / "config.yaml"
    if config_src.exists() and not same_dir:
        shutil.copy2(config_src, config_dst)


def _clean_nan(value):
    """递归清理 NaN/±Inf → None（JSON null）。

    ``json.dump`` 默认 allow_nan=True 会把 NaN 写成非法 JSON 字面量
    （``"ann_return": NaN``），下游解析会失败。所有持久化/返回前的
    metrics 必须经过清理（docs/run-backtest-data-gate.md）。
    """
    if isinstance(value, float):
        import math
        return None if not math.isfinite(value) else value
    if isinstance(value, dict):
        return {k: _clean_nan(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_nan(v) for v in value]
    return value


def save_run_metrics(run_dir: Path, metrics: dict) -> None:
    """保存 metrics.json（NaN/Inf 清理为 null，保证合法 JSON）。"""
    with open(run_dir / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(_clean_nan(metrics), f, indent=2, ensure_ascii=False)


def update_results_tsv(
    strategy_dir: Path,
    run_name: str,
    metrics: dict,
    *,
    round_num: int | None = None,
    results_tsv: Path | None = None,
) -> None:
    """Update results.tsv.

    v2: ``round`` column appended LAST (index 13) so existing column
    indices (calmar=3, status=11) stay stable; CLI rows leave it empty.
    ``results_tsv`` overrides the default location (``strategy_dir/runs/
    results.tsv``) for study scenarios.
    """
    results_path = results_tsv or (strategy_dir / "runs" / "results.tsv")

    if results_path.exists():
        with open(results_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    else:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "run\tcommit\taction\tcalmar\tsharpe\tmax_dd\t"
            "ann_return\tturnover\tfactors_added\tfactors_removed\t"
            "params_changed\tstatus\tdescription\tround\n"
        ]

    row = "\t".join([
        run_name,
        metrics.get("commit", ""),
        metrics.get("action", ""),
        str(metrics.get("calmar", 0.0)),
        str(metrics.get("sharpe", 0.0)),
        # metrics.json 里是 max_drawdown / ann_turnover; 兼容旧键名
        str(metrics.get("max_dd", metrics.get("max_drawdown", 0.0))),
        str(metrics.get("ann_return", 0.0)),
        str(metrics.get("turnover", metrics.get("ann_turnover", 0.0))),
        str(metrics.get("factors_added", 0)),
        str(metrics.get("factors_removed", 0)),
        str(metrics.get("params_changed", 0)),
        metrics.get("status", "pending"),
        metrics.get("description", ""),
        str(round_num) if round_num is not None else "",
    ]) + "\n"

    lines.append(row)

    with open(results_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ============================================================
# 回测执行 (脚本模式)
# ============================================================

def _extract_warnings(metrics: dict, output: str) -> list[str]:
    """从 metrics 和 output 中提取 warnings (用于 run_card).

    v1 极简版: 仅检测 NaN/Inf 字段, 后续可扩展.
    """
    warnings: list[str] = []
    for k, v in metrics.items():
        if isinstance(v, float) and (v != v or v == float("inf") or v == float("-inf")):
            warnings.append(f"invalid_{k}: {v!r}")
    return warnings


def run_strategy(
    strategy_dir: Path,
    timeout: int = 300,
    log_path: Path | None = None,
    env_overrides: Optional[dict] = None,
) -> tuple[bool, str]:
    """运行策略脚本（后台化：日志停滞判定，无墙钟超时）。

    ``timeout`` 语义为**日志停滞窗口**（默认 300s）：只要策略脚本持续向
    ``log_path`` 输出（run.log 流式追加），就跑多久都行；进程存活但日志
    停滞超过 timeout → 判定卡死 → 整组 kill。``log_path`` 缺省时落到
    ``strategy_dir/run.log``。

    ``env_overrides`` 在受限环境白名单之上追加键（如 SR_RUN_DIR 指标
    契约路径——框架控制的值，非机密）。
    """
    strategy_file = strategy_dir / "strategy.py"
    if not strategy_file.exists():
        return False, f"策略文件不存在: {strategy_file}"

    if log_path is None:
        log_path = strategy_dir / "run.log"

    from strategy_research.core.utils.bg_proc import log_tail, run_bg, wait_bg

    try:
        import sys
        env = _build_restricted_env()
        if env_overrides:
            env.update(env_overrides)
        proc = run_bg(
            [sys.executable, str(strategy_file)],
            log_path,
            cwd=str(strategy_dir),
            env=env,
        )
        success, output = wait_bg(proc, log_path, stall_timeout=float(timeout))
        if not success:
            # stalled → killed; surface the log tail so the caller sees
            # how far the strategy got before it went silent
            tail = log_tail(log_path, n=10)
            return False, f"策略执行停滞 ({timeout}秒无日志输出): {tail}"
        return proc.returncode == 0, output

    except Exception as e:
        return False, f"策略执行失败: {e}"


def run_backtest_script(
    workspace_path: Path,
    strategy_name: str,
    action: str = "manual",
    description: str = "",
    timeout: int = 300,
    run_dir: Path | None = None,
    strategy_dir: Path | None = None,
    results_tsv: Path | None = None,
    round_num: int | None = None,
) -> dict:
    """运行策略脚本回测并保存结果。

    Args:
        workspace_path: 工作区路径
        strategy_name: 策略名称
        action: 行动类型
        description: 描述
        timeout: 超时时间 (秒)
        run_dir: 可选的 run 目录路径。如果提供,则使用此目录而不是创建新目录。
        strategy_dir: 策略/执行目录（strategy.py/config.yaml 源）。默认
            ``workspace/strategies/<name>``；study 场景传 run 目录。
        results_tsv: results.tsv 落点。默认 ``strategy_dir/runs/results.tsv``。
        round_num: study 轮号（写入 results.tsv 的 round 列）。
    """
    if strategy_dir is None:
        strategy_dir = workspace_path / "strategies" / strategy_name
    else:
        strategy_dir = Path(strategy_dir)
    if not strategy_dir.exists():
        return {"success": False, "run": "", "metrics": {}, "error": f"策略目录不存在: {strategy_dir}"}

    if run_dir is None:
        run_name = get_next_run_name(strategy_dir)
        run_dir = create_run_dir(strategy_dir, run_name)
    else:
        run_name = run_dir.name
        # v2: caller-provided run dirs (study layout) may not exist yet
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

    save_run_snapshot(strategy_dir, run_dir)

    # Backgrounded: strategy stdout/stderr streams straight into
    # run.log (log-progress liveness, no wall-clock ceiling).
    # SR_RUN_DIR 注入指标契约路径:框架拥有的 prepare.py 在子进程内
    # 把 evaluate() 结果写到 <run_dir>/metrics_raw.json。
    success, output = run_strategy(
        strategy_dir, timeout,
        log_path=run_dir / "run.log",
        env_overrides={"SR_RUN_DIR": str(run_dir)},
    )

    # 读取因子失败信息
    factor_failures = []
    factor_failures_path = run_dir / "factor_failures.json"
    if factor_failures_path.exists():
        try:
            import json
            factor_failures = json.loads(factor_failures_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    # 也检查 strategy_dir（策略脚本运行时写入）
    strategy_failures_path = strategy_dir / "factor_failures.json"
    if strategy_failures_path.exists():
        try:
            import json
            factor_failures = json.loads(strategy_failures_path.read_text(encoding="utf-8"))
            strategy_failures_path.unlink(missing_ok=True)
        except Exception:
            pass

    # 指标来源:结构化契约优先;run.log 正则仅用于旧策略降级 + 一致性
    # 交叉校验(见上方信任模型注释)。契约与日志不一致 = 疑似指标伪造
    # 或双写源错乱 → fail-closed,不落任何结果。
    contract = read_metrics_contract(run_dir / "metrics_raw.json")
    scraped = parse_run_log(run_dir / "run.log")
    if contract is not None:
        problems = _cross_check_contract(contract, scraped)
        if problems:
            return {
                "success": False,
                "run": run_name,
                "metrics": {},
                "error": (
                    "指标完整性校验失败(契约与 run.log 不一致, 疑似指标伪造): "
                    + "; ".join(problems)
                ),
                "factor_failures": factor_failures,
            }
        metrics = dict(contract)
        metrics["metrics_source"] = "contract"
    else:
        metrics = dict(scraped)
        metrics["metrics_source"] = "run.log" if scraped else "none"

    commit_hash = git_get_hash(workspace_path)

    metrics.update({
        "run": run_name,
        "commit": commit_hash,
        "action": action,
        "description": description,
        "status": "success",
        "timestamp": datetime.now().isoformat(),
    })

    save_run_metrics(run_dir, metrics)
    update_results_tsv(
        strategy_dir, run_name, metrics,
        round_num=round_num, results_tsv=results_tsv,
    )

    save_backtest_result(
        workspace_path=workspace_path,
        strategy_name=strategy_name,
        run=run_name,
        commit_hash=commit_hash,
        action=action,
        goal_metric=metrics.get("calmar", 0.0),
        calmar=metrics.get("calmar", 0.0),
        sharpe=metrics.get("sharpe", 0.0),
        max_dd=metrics.get("max_dd", 0.0),
        ann_return=metrics.get("ann_return", 0.0),
        ann_vol=metrics.get("ann_vol", 0.0),
        sortino=metrics.get("sortino", 0.0),
        turnover=metrics.get("turnover", 0.0),
        status="success",
        description=description,
    )

    # Trust Layer: write run_card.{json,md}
    write_run_card(
        run_dir,
        config={
            "run": run_name,
            "strategy": strategy_name,
            "action": action,
            "source": metrics.get("source", ""),
        },
        metrics=metrics,
        strategy_paths=[
            run_dir / "strategy.py",
            run_dir / "config.yaml",
        ],
        warnings=_extract_warnings(metrics, output),
        data_source=metrics.get("source", ""),
        fetched_at=metrics.get("fetched_at", ""),
        data_rows=int(metrics.get("data_rows", 0)),
        cache_key=metrics.get("cache_key", ""),
    )

    return {
        "success": success,
        "run": run_name,
        "metrics": metrics,
        "error": "" if success else output,
        "factor_failures": factor_failures,
    }


# ============================================================
# 回测执行 (YAML 配置模式)
# ============================================================

def run_backtest_from_yaml(
    workspace_path: Path,
    strategy_name: str,
    yaml_path: str | None = None,
    action: str = "manual",
    description: str = "",
    strategy_dir: Path | None = None,
    results_tsv: Path | None = None,
    runs_dir: Path | None = None,
    round_num: int | None = None,
) -> dict:
    """从 YAML 配置运行回测。

    v2: ``strategy_dir`` / ``results_tsv`` / ``runs_dir`` override the
    default ``workspace/strategies/<name>`` layout for study scenarios.
    """
    if strategy_dir is None:
        strategy_dir = workspace_path / "strategies" / strategy_name
    else:
        strategy_dir = Path(strategy_dir)
    resolved_yaml = Path(yaml_path) if yaml_path is not None else strategy_dir / "config.yaml"

    if not resolved_yaml.exists():
        return {"success": False, "run": "", "metrics": {}, "error": f"配置文件不存在: {resolved_yaml}"}

    try:
        from strategy_research.apps.config_runner import run_from_yaml
        result = run_from_yaml(str(resolved_yaml), workspace_path)

        # 保存结果
        if runs_dir is None:
            runs_dir = strategy_dir / "runs"
        else:
            runs_dir = Path(runs_dir)
        run_name = get_next_run_name(runs_dir)
        run_dir = runs_dir / run_name
        run_dir.mkdir(parents=True, exist_ok=True)

        save_run_snapshot(strategy_dir, run_dir)

        # 保存 metrics.json
        metrics = result.metrics.copy()
        metrics.update({
            "run": run_name,
            "commit": git_get_hash(workspace_path),
            "action": action,
            "description": description,
            "status": "success",
            "timestamp": datetime.now().isoformat(),
        })

        # 运行期失败暴露: 完整因子失败列表落盘 factor_failures.json
        # （与脚本路径既有格式一致, 可 read_file 审计）;
        # metrics.json 存聚合摘要（按因子, 最多 5 条）。
        factor_failures = list(getattr(result, "factor_failures", None) or [])
        if factor_failures:
            try:
                with open(run_dir / "factor_failures.json", "w", encoding="utf-8") as f:
                    json.dump(_clean_nan(factor_failures), f, indent=2, ensure_ascii=False)
            except Exception:  # noqa: BLE001
                logger.warning("failed to write factor_failures.json", exc_info=True)
            metrics["factor_failures_summary"] = _factor_failures_summary(factor_failures)

        # 运行期其他警告（equity_curve.csv 导出失败等）统一收集
        metrics["warnings"] = list(getattr(result, "warnings", None) or [])

        save_run_metrics(run_dir, metrics)
        update_results_tsv(
            strategy_dir, run_name, metrics,
            round_num=round_num, results_tsv=results_tsv,
        )

        # 保存到 DuckDB
        save_backtest_result(
            workspace_path=workspace_path,
            strategy_name=strategy_name,
            run=run_name,
            commit_hash=metrics.get("commit", ""),
            action=action,
            goal_metric=metrics.get("calmar", 0.0),
            calmar=metrics.get("calmar", 0.0),
            sharpe=metrics.get("sharpe", 0.0),
            max_dd=metrics.get("max_dd", 0.0),
            ann_return=metrics.get("ann_return", 0.0),
            ann_vol=metrics.get("ann_vol", 0.0),
            sortino=metrics.get("sortino", 0.0),
            turnover=metrics.get("turnover", 0.0),
            status="success",
            description=description,
        )

        # 保存权重历史和 NAV 历史到 DuckDB
        save_weight_history(workspace_path, strategy_name, run_name, result.weights_history)
        save_nav_history(workspace_path, strategy_name, run_name, result.nav_daily)

        # 导出净值曲线到文件 (equity_curve.csv)。这是 show_chart /
        # show_report 的文件引用源：agent 只拿到路径，nav 数据不进
        # LLM 上下文（docs/right-panel-agent-driven.md）。
        export_warning = ""
        try:
            nav = result.nav_daily
            if nav is not None and len(nav) > 0:
                nav_df = pd.DataFrame({
                    "date": pd.to_datetime(nav.index).strftime("%Y-%m-%d"),
                    "nav": nav.values,
                })
                nav_df.to_csv(run_dir / "equity_curve.csv", index=False)
        except Exception as exc:  # noqa: BLE001
            export_warning = f"equity_curve.csv 导出失败: {exc}"
            logger.warning(f"⚠️ {export_warning}")
        if export_warning:
            metrics["warnings"] = list(metrics.get("warnings", [])) + [export_warning]
            save_run_metrics(run_dir, metrics)

        # Trust Layer: write run_card.{json,md}
        write_run_card(
            run_dir,
            config={
                "run": run_name,
                "strategy": strategy_name,
                "action": action,
                "source": metrics.get("source", ""),
            },
            metrics=metrics,
            strategy_paths=[
                run_dir / "strategy.py",
                run_dir / "config.yaml",
            ],
            data_source=metrics.get("source", ""),
            fetched_at=metrics.get("fetched_at", ""),
            data_rows=int(metrics.get("data_rows", 0)),
            cache_key=metrics.get("cache_key", ""),
        )

        return {
            "success": True,
            "run": run_name,
            "metrics": metrics,
            "nav": result.nav_daily,
            "factor_failures": factor_failures,
        }

    except Exception as e:
        return {"success": False, "run": "", "metrics": {}, "error": str(e)}


def _factor_failures_summary(failures: list[dict], max_items: int = 5) -> list[dict]:
    """按因子聚合失败信息（每因子一条: 失败资产数 + 首个错误），最多 5 条。"""
    by_factor: dict[str, dict] = {}
    for rec in failures:
        key = rec.get("factor", "unknown")
        if key not in by_factor:
            by_factor[key] = {
                "factor": key,
                "failed_assets": 0,
                "first_error": rec.get("error", "")[:200],
            }
        by_factor[key]["failed_assets"] += 1
    items = list(by_factor.values())[:max_items]
    if len(by_factor) > max_items:
        items.append({"factor": "...", "failed_assets": len(by_factor) - max_items,
                      "first_error": f"等 {len(by_factor) - max_items} 个因子"})
    return items


# ============================================================
# 实验评估
# ============================================================

def evaluate_experiment(
    workspace_path: Path,
    strategy_name: str,
    run_name: str,
    status: str = "keep",
) -> bool:
    """评估实验结果并更新状态。"""
    strategy_dir = workspace_path / "strategies" / strategy_name
    run_dir = strategy_dir / "runs" / run_name

    if not run_dir.exists():
        return False

    metrics_path = run_dir / "metrics.json"
    if not metrics_path.exists():
        return False

    with open(metrics_path, "r", encoding="utf-8") as f:
        metrics = json.load(f)

    metrics["status"] = status

    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)

    update_results_tsv(strategy_dir, run_name, metrics)

    from strategy_research.apps.db import get_connection
    conn = get_connection(workspace_path)
    if conn is not None:
        try:
            conn.execute("""
                UPDATE backtest_results
                SET status = ?
                WHERE strategy_name = ? AND run = ?
            """, [status, strategy_name, run_name])
            conn.close()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Failed to update backtest status: %s", e)

    # Git-as-log: 每次 run 都 commit (不只是 keep)
    git_commit_rich(
        workspace_path=workspace_path,
        strategy_name=strategy_name,
        run_name=run_name,
        status=status,
        metrics=metrics,
        action=metrics.get("action", ""),
        hypothesis=metrics.get("hypothesis", ""),
    )

    return True


# ============================================================
# 实验历史
# ============================================================

def get_experiment_history(
    workspace_path: Path,
    strategy_name: str,
    limit: int = 20,
) -> list[dict]:
    """获取实验历史。"""
    strategy_dir = workspace_path / "strategies" / strategy_name
    results_path = strategy_dir / "runs" / "results.tsv"

    if not results_path.exists():
        return []

    with open(results_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if len(lines) <= 1:
        return []

    header = lines[0].rstrip("\n").split("\t")

    experiments = []
    for line in lines[1:limit + 1]:
        # 用 rstrip 保留 trailing empty (zip header 长度对齐)
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= len(header):
            exp = dict(zip(header, parts))
            experiments.append(exp)

    return experiments


def get_best_experiment(
    workspace_path: Path,
    strategy_name: str,
    goal_metric: str = "calmar",
) -> Optional[dict]:
    """获取最佳实验。"""
    experiments = get_experiment_history(workspace_path, strategy_name)
    if not experiments:
        return None

    keeps = [e for e in experiments if e.get("status") == "keep"]
    if not keeps:
        return None

    def get_metric(exp):
        try:
            return float(exp.get(goal_metric, 0))
        except (ValueError, TypeError):
            return 0

    return max(keeps, key=get_metric)
