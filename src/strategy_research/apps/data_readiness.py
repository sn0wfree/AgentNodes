"""run_backtest 数据就绪性检查核心（纯只读，零副作用）。

供两类入口复用:
- ``CheckDataTool``（check_data 工具）: 完整报告 C1~C7，供 data_quality agent 决策
- ``RunBacktestTool`` 内置门禁: 轻量 C1~C6（include_cleaning=False），
  不可跑时在创建 run 之前拦截并返回可行动诊断

设计要点（docs/run-backtest-data-gate.md）:
- 只读: 不删、不写、不 fetch —— 修复动作由 LLM 自行决策
  （get_market_data / clean_data / 改 config）
- C2 只管「codes 覆盖」维度; C5 只管「codes 内资产质量」——不重叠
- C6 为语法级检查（_tokenize 零数据依赖、100% 覆盖全部因子）
- C7 复用 tools.data_clean.clean_data(dry_run=True) 统计（try/except 防御）
- 报告大小控制: 每项 detail/fix_hint ≤200 字符; 列表截断前 N 项
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

_HEAD = 200
_LIST_MAX = 10


def _fmt_list(items: list[str], max_items: int = _LIST_MAX) -> str:
    """截断列表: 前 N 项 + 等 X 项。"""
    if not items:
        return "无"
    head = "、".join(items[:max_items])
    if len(items) > max_items:
        head += f" 等 {len(items)} 项"
    return head


def _truncate(text: str, max_len: int = _HEAD) -> str:
    text = str(text).strip()
    return text[:max_len] + "..." if len(text) > max_len else text


@dataclass
class ReadinessCheck:
    id: str
    name: str
    status: str  # ok | warn | fail
    detail: str = ""
    fix_hint: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "fix_hint": self.fix_hint,
        }


@dataclass
class ReadinessReport:
    ok: bool
    checks: list[ReadinessCheck] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "checks": [c.to_dict() for c in self.checks]}


def _db(workspace_path: Path):
    from strategy_research.core.db import get_connection

    return get_connection(workspace_path, read_only=True)


def _asset_stats(workspace_path: Path, strategy_name: str) -> dict[str, dict] | None:
    """该策略名下每资产的 {count, min_date, max_date}。DB 不可用返回 None。"""
    conn = _db(workspace_path)
    if conn is None:
        return None
    try:
        df = conn.execute(
            """
            SELECT asset_code,
                   COUNT(*) AS n,
                   MIN(date) AS min_date,
                   MAX(date) AS max_date
            FROM price_data
            WHERE strategy_name = ?
            GROUP BY asset_code
            ORDER BY asset_code
            """,
            [strategy_name],
        ).fetchdf()
    finally:
        conn.close()
    if df is None or df.empty:
        return {}
    return {
        str(rec["asset_code"]): {
            "count": int(rec["n"]),
            "min_date": rec["min_date"],
            "max_date": rec["max_date"],
        }
        for rec in df.to_dict("records")
    }


def _check_assets(
    workspace_path: Path, strategy_name: str, cfg: dict | None,
    codes: list[str] | None, start_date: str | None, end_date: str | None,
) -> tuple[list[ReadinessCheck], dict | None]:
    """C1 资产覆盖 / C2 孤儿资产 / C3 窗口 / C4 覆盖密度。"""
    stats = _asset_stats(workspace_path, strategy_name)
    checks: list[ReadinessCheck] = []
    if stats is None:
        checks.append(ReadinessCheck(
            id="C0", name="数据库可用性", status="fail",
            detail="无法打开 workspace DuckDB",
            fix_hint="确认 workspace 存在 data.duckdb；无数据则先 get_market_data",
        ))
        return checks, None

    if not codes:
        checks.append(ReadinessCheck(
            id="C1", name="资产覆盖", status="warn",
            detail="config 未声明 data.codes，跳过覆盖/窗口/密度检查",
            fix_hint="在 config.yaml 的 data.codes 声明资产列表，或将 check_data 的 source 设为 explicit",
        ))
        return checks, stats

    known = set(stats.keys())
    want = set(codes)

    # C1 资产覆盖
    missing = sorted(want - known)
    if missing:
        checks.append(ReadinessCheck(
            id="C1", name="资产覆盖", status="fail",
            detail=f"DB 缺少 {len(missing)} 只配置资产: {_fmt_list(missing)}",
            fix_hint=(
                f"缺 {len(missing)} 只配置资产（{_fmt_list(missing[:5])}），"
                f"回测将只覆盖 {len(want) - len(missing)}/{len(want)} 只："
                f"get_market_data(codes={missing[:5]}, "
                f"strategy_name='<当前策略名>') 补齐后重试"
            ),
        ))
    else:
        checks.append(ReadinessCheck(
            id="C1", name="资产覆盖", status="ok",
            detail=f"{len(want)} 只配置资产全部有数据",
        ))

    # C2 孤儿资产 (warn: 不阻塞; Commit 1 的 codes 过滤后不再污染结果)
    orphans = sorted(known - want)
    if orphans:
        detail_parts = [f"{c}×{stats[c]['count']}" for c in orphans]
        checks.append(ReadinessCheck(
            id="C2", name="孤儿资产", status="warn",
            detail=f"DB 存在 {len(orphans)} 只不在 config.codes 的资产: {_fmt_list(detail_parts)}",
            fix_hint="历史残留数据；回测不受影响（已按 codes 过滤），可忽略或用清理脚本移除",
        ))

    # C3 窗口/新鲜度
    if start_date and end_date:
        all_min = min((stats[c]["min_date"] for c in codes if c in stats), default=None)
        all_max = max((stats[c]["max_date"] for c in codes if c in stats), default=None)
        if all_min is None:
            checks.append(ReadinessCheck(
                id="C3", name="窗口/新鲜度", status="fail",
                detail="配置资产均无数据（窗口无法评估）",
                fix_hint=f"get_market_data(start_date='{start_date}', end_date='{end_date}', strategy_name='{strategy_name}')",
            ))
        else:
            all_min_s = str(all_min)[:10]  # 只取 YYYY-MM-DD 部分
            all_max_s = str(all_max)[:10]
            short_front = all_min_s > start_date
            short_back = all_max_s < end_date
            if short_front or short_back:
                gap_parts = []
                if short_front:
                    gap_parts.append(
                        f"起始滞后（DB 自 {all_min_s} 起，配置要求自 {start_date} 起）"
                    )
                if short_back:
                    gap_parts.append(
                        f"结尾截断（DB 至 {all_max_s}，配置要求至 {end_date}）"
                    )
                gap = "；".join(gap_parts)
                checks.append(ReadinessCheck(
                    id="C3", name="窗口/新鲜度", status="fail",
                    detail=f"DB 窗口 {all_min_s} ~ {all_max_s} vs 期望 {start_date} ~ {end_date} ({gap})",
                    fix_hint=(
                        f"数据窗口不足（{gap}）：get_market_data("
                        f"start_date='{start_date}', end_date='{end_date}', "
                        f"strategy_name='<当前策略名>') 补齐；"
                        f"或调整 config.yaml 的 data.start_date/end_date"
                    ),
                ))
            else:
                checks.append(ReadinessCheck(
                    id="C3", name="窗口/新鲜度", status="ok",
                    detail=f"DB 窗口 {all_min_s} ~ {all_max_s} 覆盖期望范围",
                ))

    # C4 覆盖密度
    min_history = (cfg or {}).get("rebalance", {}).get("min_history", 252)
    sparse: list[str] = []
    tiny: list[str] = []
    for c in codes:
        n = stats.get(c, {}).get("count", 0)
        if n <= 2:
            tiny.append(f"{c}×{n}")
        elif n < min_history:
            sparse.append(f"{c}×{n}")
    if tiny:
        checks.append(ReadinessCheck(
            id="C4", name="覆盖密度", status="fail",
            detail=f"资产行数 ≤2（无法计算指标）: {_fmt_list(tiny)}",
            fix_hint="重拉数据（get_market_data）修复残留资产；确认 codes 正确",
        ))
    elif sparse:
        checks.append(ReadinessCheck(
            id="C4", name="覆盖密度", status="warn",
            detail=f"资产行数 < min_history({min_history}): {_fmt_list(sparse)}",
            fix_hint="回测可运行但预热不足结果不可信；重拉数据或调整 rebalance.min_history",
        ))
    else:
        checks.append(ReadinessCheck(
            id="C4", name="覆盖密度", status="ok",
            detail=f"配置资产行数均 ≥ min_history({min_history})",
        ))
    return checks, stats


def _check_quality(
    workspace_path: Path, strategy_name: str, codes: list[str] | None,
) -> list[ReadinessCheck]:
    """C5 数据质量: codes 内资产 OHLC 全 NaN 行（聚合 SQL，不加载全量）。"""
    if not codes:
        return []
    conn = _db(workspace_path)
    if conn is None:
        return []
    try:
        placeholders = ", ".join(["?" for _ in codes])
        df = conn.execute(
            f"""
            SELECT asset_code,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (
                       WHERE open IS NULL AND high IS NULL
                         AND low IS NULL AND close IS NULL
                   ) AS nan_rows
            FROM price_data
            WHERE strategy_name = ? AND asset_code IN ({placeholders})
            GROUP BY asset_code
            """,
            [strategy_name] + codes,
        ).fetchdf()
    except Exception:
        return []
    finally:
        conn.close()
    if df is None or df.empty:
        return []
    bad: list[str] = []
    for row in df.itertuples():
        if row.nan_rows and row.nan_rows == row.total:
            bad.append(f"{row.asset_code}×{row.total}")
    if bad:
        return [ReadinessCheck(
            id="C5", name="数据质量", status="fail",
            detail=f"codes 内资产存在 OHLC 全 NaN 行: {_fmt_list(bad)}",
            fix_hint="该资产数据为残留/脏数据，用 get_market_data 重拉",
        )]
    return [ReadinessCheck(
        id="C5", name="数据质量", status="ok",
        detail="codes 内资产无 OHLC 全 NaN 行",
    )]


def _check_factor_syntax(cfg: dict | None) -> list[ReadinessCheck]:
    """C6 因子语法: 全部因子 _tokenize 语法检查（零数据依赖、100% 覆盖）。"""
    factors = (cfg or {}).get("factors", [])
    if not factors:
        return [ReadinessCheck(
            id="C6", name="因子语法", status="warn",
            detail="config 未声明 factors",
            fix_hint="在 config.yaml 的 factors 声明因子表达式",
        )]
    from strategy_research.core.compute_factor import _tokenize

    bad: list[str] = []
    for f in factors:
        # Handle both dict and string factor formats
        if isinstance(f, str):
            code = f
        elif isinstance(f, dict):
            code = f.get("code", "")
        else:
            continue
        if not code:
            continue
        try:
            tokens = _tokenize(code)
            # _tokenize 是纯词法（不校验括号配对）——补一层括号平衡检查
            depth = 0
            for t in tokens:
                if getattr(t, "value", "") == "(":
                    depth += 1
                elif getattr(t, "value", "") == ")":
                    depth -= 1
                    if depth < 0:
                        raise ValueError("括号不匹配")
            if depth != 0:
                raise ValueError("括号不匹配")
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{f.get('name', '?')}: {_truncate(str(exc), 80)}")
    if bad:
        return [ReadinessCheck(
            id="C6", name="因子语法", status="fail",
            detail=f"因子表达式语法错误: {_fmt_list(bad)}",
            fix_hint="修正 config.yaml 的 factors 表达式（如 ts_return(close, 20)）",
        )]
    return [ReadinessCheck(
        id="C6", name="因子语法", status="ok",
        detail=f"{len(factors)} 个因子表达式语法通过",
    )]


def _check_cleaning(
    workspace_path: Path, strategy_name: str,
) -> list[ReadinessCheck]:
    """C7 行级质量: 复用 clean_data(dry_run=True) 统计（只读）。

    依赖 clean_data 的 dry_run 语义（不写库）——try/except 防御，失败降级为
    warn 不阻塞。
    """
    try:
        # 一级点号 = core/；两级会解析到 strategy_research.tools（不存在）。
        # 此前是 ``..tools``，于是 C7 每条路径都抛 ImportError，被下面的
        # ``except Exception`` 吞成 "行级统计不可用" 的 warn——**C7 检查
        # 从未真正执行过**（2026-11-02 补测发现；与 MCP validate_run、
        # scheduled_research/cli 的同一个错误类）。
        from strategy_research.apps.tools.data_clean import clean_data
        from strategy_research.core.db import get_connection

        conn = get_connection(workspace_path, read_only=True)
        if conn is None:
            return []
        try:
            df = conn.execute(
                "SELECT date, asset_code AS asset, open, high, low, close, volume "
                "FROM price_data WHERE strategy_name = ?",
                [strategy_name],
            ).fetch_df()
        finally:
            conn.close()
        if df.empty:
            return []
        # clean_data 返回 ``(cleaned_df, CleaningReport)`` 二元组——此前当成
        # 单个对象直接取 .duplicates_removed，必然 AttributeError，然后被
        # 下面的 except 吞成 warn。两处一起导致 C7 永远是「不可用」。
        _cleaned, report = clean_data(df, "standard", None, None, dry_run=True)
        issues: list[str] = []
        if report.duplicates_removed:
            issues.append(f"重复行 {report.duplicates_removed}")
        if report.missing_filled:
            issues.append(f"缺失填充 {report.missing_filled}")
        if report.outliers_detected:
            issues.append(f"异常值 {report.outliers_detected}")
        if not issues:
            return [ReadinessCheck(
                id="C7", name="行级质量", status="ok",
                detail="无重复行/缺失/异常值",
            )]
        return [ReadinessCheck(
            id="C7", name="行级质量", status="warn",
            detail="; ".join(issues),
            fix_hint="clean_data(strategy_name='<当前策略名>', preset='standard') 清洗",
        )]
    except Exception as exc:  # noqa: BLE001
        return [ReadinessCheck(
            id="C7", name="行级质量", status="warn",
            detail=f"行级统计不可用: {_truncate(str(exc), 100)}",
            fix_hint="可忽略；或手动检查数据",
        )]


def check_data_readiness(
    workspace_path: Path,
    strategy_name: str,
    cfg: dict | None = None,
    codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    include_cleaning: bool = False,
) -> ReadinessReport:
    """数据就绪性检查（只读）。cfg 提供 codes/start/end/min_history/factors；
    显式 codes/dates 参数优先（source='explicit' 场景）。"""
    workspace_path = Path(workspace_path)
    checks: list[ReadinessCheck] = []

    if cfg is not None:
        data_cfg = cfg.get("data", {}) or {}
        codes = codes if codes is not None else data_cfg.get("codes")
        start_date = start_date or data_cfg.get("start_date")
        end_date = end_date or data_cfg.get("end_date")

    asset_checks, stats = _check_assets(
        workspace_path, strategy_name, cfg, codes, start_date, end_date,
    )
    checks.extend(asset_checks)
    if stats is not None and codes:
        checks.extend(_check_quality(workspace_path, strategy_name, codes))
    checks.extend(_check_factor_syntax(cfg))
    if include_cleaning:
        checks.extend(_check_cleaning(workspace_path, strategy_name))

    ok = all(c.status != "fail" for c in checks)
    return ReadinessReport(ok=ok, checks=checks)


__all__ = [
    "ReadinessCheck",
    "ReadinessReport",
    "check_data_readiness",
]
