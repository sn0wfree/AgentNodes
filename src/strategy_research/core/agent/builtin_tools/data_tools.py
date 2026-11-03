"""Agent tools for market data: get_market_data, list_data_sources, search_symbol."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..tools import EFFECT_DB, EFFECT_NET, BaseTool, ToolContext, ToolRegistry
from .utils import err_actionable, tool_ok, try_unwrap_dict, try_unwrap_list

logger = logging.getLogger(__name__)


def _summarize_fetch_result(data: dict) -> tuple[dict, dict, int]:
    """Build compact per-code summary + 5-row preview; returns (summary, preview, total_rows).

    The full rows must NOT enter the LLM prompt (context-overflow root
    cause; see docs/context-overflow-fix.md).
    """
    summary: dict[str, Any] = {}
    preview: dict[str, Any] = {}
    total_rows = 0
    for code, df in data.items():
        if df is None or df.empty:
            summary[code] = {"rows": 0, "status": "empty"}
            continue
        n_rows = len(df)
        total_rows += n_rows
        close = df["close"] if "close" in df.columns else None
        volume = df["volume"] if "volume" in df.columns else None
        s = {"rows": n_rows, "status": "ok"}
        if close is not None and not close.empty:
            s["first_close"] = float(close.iloc[0])
            s["last_close"] = float(close.iloc[-1])
            s["close_min"] = float(close.min())
            s["close_max"] = float(close.max())
        if volume is not None and not volume.empty:
            s["avg_volume"] = float(volume.mean())
        summary[code] = s
        preview_rows = []
        for _, row in df.head(5).iterrows():
            rec: dict[str, Any] = {}
            for col in df.columns:
                val = row[col]
                if hasattr(val, "isoformat"):
                    rec[str(col)] = val.isoformat()
                elif hasattr(val, "item"):
                    rec[str(col)] = val.item()
                else:
                    rec[str(col)] = val
            preview_rows.append(rec)
        preview[code] = preview_rows
    return summary, preview, total_rows


# ── 1. GetMarketDataTool ────────────────────────────────────────


class GetMarketDataTool(BaseTool):
    """获取 OHLCV 行情并持久化到工作区 DuckDB（一步完成）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext; 副作用改 effects)
    #
    # ## 用途
    # 按 fallback 链获取 OHLCV 行情, persist=True (默认) 直接写入
    # DuckDB price_data (回测/因子立即可用), 返回摘要+预览;
    # 全量数据不进 LLM prompt (context 安全)。
    #
    # ## 参数
    # - codes: 资产代码列表 (必填, 如 ['600519.SH','000858.SZ'])
    # - start_date/end_date: ISO 日期 (必填)
    # - interval: K 线周期 (默认 '1D')
    # - source: 数据源覆盖 (可选)
    # - max_rows: 每代码最大行数 (默认 500)
    # - persist: 是否入库 (默认 True; False 只查看)
    # - strategy_name: 数据分区名 (默认 'default')
    # - force_refresh: 跳过缓存强制网络取数 (默认 False)
    #
    # ## 示例
    # {"codes": ["600519.SH"], "start_date": "2023-01-01", "end_date": "2023-12-31"}
    #
    # ## 边界
    # 写工具 (effects: db + net); 幂等 (INSERT OR REPLACE);
    # 纯数字代码会误判为 FRED/macro, A 股务必带后缀。
    #
    # ## 错误处理范式
    # - 缺 codes/日期 → error + expected 示例
    # - 日期范围非法 → error + 校验说明
    # - 指定 source 不可用 → error + 可用源列表
    # - 网络失败 → error (transient, 可重试)
    # - persist=True 幂等, 重试安全
    #
    # ## 相关工具
    # run_backtest/compute_factor/factor_*: 数据消费方
    # ─────────────────────────────────────────────
    """

    name = "get_market_data"
    description = (
        "获取 OHLCV 行情, persist=True (默认) 一步写入 DuckDB; 返回摘要+预览, "
        "全量数据不进 prompt。"
    )
    repeatable = True
    category = "行情"
    effects = frozenset({EFFECT_DB, EFFECT_NET})

    def execute(
        self,
        ctx: ToolContext,
        codes: list[str],
        start_date: str,
        end_date: str,
        interval: str = "1D",
        source: str | None = None,
        max_rows: int = 500,
        persist: bool = True,
        strategy_name: str = "default",
        force_refresh: bool = False,
    ) -> str:
        from ...data_source.base import validate_date_range
        from ...data_source.registry import (
            LOADER_REGISTRY,
            NoAvailableSourceError,
            resolve_loader,
        )
        from ...data_source.utils import detect_market

        # Defensive reads: framework coercion handles JSON-string lists and
        # single-key wrapping; "A,B,C" comma strings are split here.
        raw_codes = codes
        if isinstance(raw_codes, str):
            codes = [c.strip() for c in raw_codes.split(",") if c.strip()]
        elif not isinstance(raw_codes, list):
            return err_actionable(
                f"codes parameter has wrong shape: {type(raw_codes).__name__}",
                received=raw_codes,
                expected="list[str] of asset codes, e.g. ['000001.SZ', '600519.SH']",
                fix="pass codes as a JSON array string OR a list, e.g. "
                    "codes=['000001.SZ', '600519.SH']",
                tool="get_market_data",
            )

        strategy_name = strategy_name or "default"
        workspace = ctx.workspace
        if persist and workspace is None:
            return err_actionable(
                "missing workspace context (required when persist=True)",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="get_market_data",
            )

        if not codes:
            return err_actionable(
                "codes is required and must be non-empty",
                expected="list[str] of asset codes, e.g. ['000001.SZ', '600519.SH']",
                fix="pass at least one code, e.g. codes=['600519.SH']",
                tool="get_market_data",
            )
        if not start_date or not end_date:
            return err_actionable(
                "start_date and end_date are required",
                expected="ISO date strings, e.g. start_date='2023-01-01'",
                fix="pass both, e.g. start_date='2023-01-01', end_date='2023-12-31'",
                tool="get_market_data",
            )

        try:
            validate_date_range(start_date, end_date)
        except ValueError as exc:
            return err_actionable(
                str(exc),
                received={"start_date": start_date, "end_date": end_date},
                expected="valid date range, e.g. start_date='2023-01-01', end_date='2023-12-31'",
                fix="ensure start_date is before end_date and both are valid ISO dates",
                tool="get_market_data",
            )

        try:
            if source and source in LOADER_REGISTRY:
                loader = LOADER_REGISTRY[source]()
                if not loader.is_available():
                    return err_actionable(
                        f"source '{source}' is not available",
                        received=source,
                        expected="one of " + ", ".join(sorted(LOADER_REGISTRY.keys())),
                        fix="either omit `source` to use auto-detection, or pick an available one",
                        tool="get_market_data",
                    )
                effective_source = source
            else:
                market = detect_market(codes[0])
                loader = resolve_loader(market)
                effective_source = loader.name

            data = loader.fetch(codes, start_date, end_date, interval=interval,
                               force_refresh=force_refresh)

            # Build compact summary + small preview. The full rows must NOT
            # enter the LLM prompt (context-overflow root cause; see
            # docs/context-overflow-fix.md).
            summary, preview, total_rows = _summarize_fetch_result(data)

            persisted_rows = 0
            if persist:
                if not workspace:
                    return err_actionable(
                        "persist=True requires 'workspace' (auto-injected by AgentLoop)",
                        expected="workspace root path, e.g. '/home/user/research'",
                        fix="persist=True is the default; if you only need to inspect "
                            "data, set persist=False instead",
                        tool="get_market_data",
                    )
                from ...db import save_ohlcv_to_db

                persisted_rows = save_ohlcv_to_db(
                    Path(workspace), data, strategy_name=strategy_name
                )

            return tool_ok({
                "summary": summary,
                "preview": preview,
                "persisted": persist,
                "strategy_name": strategy_name,
                "persisted_rows": persisted_rows,
                "meta": {
                    "codes": codes,
                    "start_date": start_date,
                    "end_date": end_date,
                    "interval": interval,
                    "source": effective_source,
                    "total_rows": total_rows,
                },
            })

        except NoAvailableSourceError as exc:
            return err_actionable(
                f"no available data source: {exc}",
                received=codes,
                expected="list of asset codes with a registered data source",
                fix="use list_data_sources() to see what's available, or check your network",
                tool="get_market_data",
            )
        except Exception as exc:
            logger.exception("get_market_data failed")
            return err_actionable(
                f"fetch failed: {exc}",
                received={"codes": codes, "start_date": start_date, "end_date": end_date},
                expected="valid codes + date range",
                fix="verify codes are correct, dates are valid, and a data source is registered",
                tool="get_market_data",
            )


# ── 2. ListDataSourcesTool ──────────────────────────────────────


class ListDataSourcesTool(BaseTool):
    """列出可用数据源及其状态。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名)
    #
    # ## 用途
    # 列出全部注册数据源: 可用性/适用市场/是否需要 API key。
    # 取数前先查可用源, 或排障时确认数据源状态。
    #
    # ## 参数
    # 无
    #
    # ## 示例
    # {}
    #
    # ## 边界
    # 只读工具; 不访问网络。
    #
    # ## 错误处理范式
    # 无输入参数, 极少失败; 失败均可安全重试。
    #
    # ## 相关工具
    # get_market_data: 用可用源取数
    # ─────────────────────────────────────────────
    """

    name = "list_data_sources"
    description = "列出已注册数据源及可用性/市场/API key 要求。"
    repeatable = True
    category = "行情"

    def execute(
        self,
        ctx: ToolContext,
    ) -> str:
        from ...data_source.registry import LOADER_REGISTRY, _ensure_registered

        _ensure_registered()
        sources = []
        for name, cls in LOADER_REGISTRY.items():
            try:
                instance = cls()
                available = instance.is_available()
                markets = list(getattr(instance, "markets", set()))
                requires_auth = getattr(instance, "requires_auth", False)
            except Exception:
                available = False
                markets = []
                requires_auth = False
            sources.append({
                "name": name,
                "available": available,
                "markets": markets,
                "requires_auth": requires_auth,
            })

        return tool_ok({
            "n_sources": len(sources),
            "sources": sources,
        })


# ── 3. SearchSymbolTool ─────────────────────────────────────────


class SearchSymbolTool(BaseTool):
    """按名称/代码搜索证券代码（A 股主）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名)
    #
    # ## 用途
    # 按名称或代码模糊搜索证券 (A 股主, 经 akshare spot 数据)。
    #
    # ## 参数
    # - query: 查询词 (必填, 名称或代码)
    # - market: 市场过滤 (默认 'a_share')
    # - limit: 最大结果数 (默认 10)
    #
    # ## 示例
    # {"query": "茅台"}
    #
    # ## 边界
    # 只读工具; 依赖 akshare 与网络; 无匹配返回空列表 (非错误)。
    #
    # ## 错误处理范式
    # - 缺 query → error + expected 示例
    # - akshare 未装 → fix 安装
    # - 网络失败 → error + fix 换查询词/检查网络
    #
    # ## 相关工具
    # get_market_data: 搜到的代码直接取数
    # ─────────────────────────────────────────────
    """

    name = "search_symbol"
    description = "按名称/代码模糊搜索证券代码 (A 股主, akshare)。"
    repeatable = True
    category = "行情"

    def execute(
        self,
        ctx: ToolContext,
        query: str,
        market: str = "a_share",
        limit: int = 10,
    ) -> str:
        query = query or ""
        market = market or "a_share"

        if not query:
            return err_actionable(
                "query is required",
                expected="non-empty string, e.g. 'maotai' or '600519'",
                fix="pass a non-empty query, e.g. query='maotai'",
                tool="search_symbol",
            )

        try:
            import akshare as ak
            # A-share: use spot data for fuzzy search
            df = ak.stock_zh_a_spot_em()
            if df is None or df.empty:
                return tool_ok({"results": [], "query": query, "market": market})

            # Fuzzy match: query in code or name
            mask = (
                df["代码"].str.contains(query, case=False, na=False)
                | df["名称"].str.contains(query, case=False, na=False)
            )
            matched = df[mask].head(limit)

            results = []
            for _, row in matched.iterrows():
                results.append({
                    "code": row.get("代码", ""),
                    "name": row.get("名称", ""),
                    "market": "a_share",
                    "price": row.get("最新价"),
                    "change_pct": row.get("涨跌幅"),
                })

            return tool_ok({
                "results": results,
                "query": query,
                "market": market,
                "limit": limit,
                "n_results": len(results),
            })

        except ImportError:
            return err_actionable(
                "akshare not installed",
                fix="install with: pip install akshare",
                tool="search_symbol",
            )
        except Exception as exc:
            logger.warning("search_symbol failed for %r: %s", query, exc)
            return err_actionable(
                f"search failed: {exc}",
                received=query,
                fix="try a different query, or check that akshare is installed and network is up",
                tool="search_symbol",
            )


# ── 4. ImportDataTool ────────────────────────────────────────


class ImportDataTool(BaseTool):
    """手动导入 OHLCV 数据到 DuckDB（非推荐主流程）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext)
    #
    # ## 用途
    # 手动/外部 OHLCV 数据导入 DuckDB。主流程是
    # get_market_data(persist=True); 本工具仅用于粘贴外部数据/CSV。
    #
    # ## 参数
    # - data: {asset_code: [记录列表]} (必填)
    # - strategy_name: 数据分区名 (默认 'default')
    #
    # ## 示例
    # {"data": {"600519.SH": [{"trade_date": "2023-12-11", "close": 1544.5}]}}
    #
    # ## 边界
    # 写工具 (effects: db); 支持 LLM 错误包裹 (JSON 字符串/单键 dict) 容错。
    #
    # ## 错误处理范式
    # - 缺 data → error + expected 结构示例
    # - 数据形状错误 → error + fix 提示用 get_market_data
    # - 均可安全重试 (INSERT OR REPLACE 幂等)
    #
    # ## 相关工具
    # get_market_data: 推荐主流程
    # ─────────────────────────────────────────────
    """

    name = "import_data"
    description = "手动导入 OHLCV 数据到 DuckDB (非推荐主流程; 主流程 get_market_data)。"
    repeatable = True
    category = "行情"
    effects = frozenset({EFFECT_DB})

    def execute(
        self,
        ctx: ToolContext,
        data: dict[str, Any],
        strategy_name: str = "default",
    ) -> str:
        workspace = ctx.workspace
        if not workspace:
            return err_actionable(
                "missing workspace context",
                expected="workspace path (AgentLoop 注入)",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="import_data",
            )

        # Defensive read: framework coercion handles JSON-string dicts;
        # single-key wrapping ("data": {...}) is unwrapped here.
        raw_data = data
        unwrapped = try_unwrap_dict(raw_data) if isinstance(raw_data, dict) else None
        data = unwrapped if unwrapped is not None else raw_data

        if not data:
            return err_actionable(
                "missing or invalid 'data' (expect dict from get_market_data)",
                expected="non-empty dict, e.g. {'600519.SH': [{'trade_date': '2023-12-11', 'close': 1544.555, ...}, ...]}",
                fix=(
                    "call get_market_data(codes=['600519.SH'], "
                    "start_date='2023-01-01', end_date='2023-12-31', "
                    "persist=True) to fetch and persist into DuckDB in one step"
                ),
                tool="import_data",
            )

        strategy_name = strategy_name or "default"

        try:
            from pathlib import Path

            from ...db import get_connection, init_db

            ws = Path(workspace)
            init_db(ws)

            conn = get_connection(ws)
            if conn is None:
                return err_actionable(
                    "failed to open DuckDB",
                    received=str(workspace),
                    expected="writable workspace path",
                    fix="run `quantnodes-research init` first, or check workspace permissions",
                    tool="import_data",
                )

            # Defensive unwrap per-code: LLM may wrap records in single-key
            # object like {"item": [...]} or {"data": [...]}.
            total_rows = 0
            for code, records in data.items():
                if isinstance(records, dict):
                    unwrapped = try_unwrap_list(records)
                    if unwrapped is None:
                        return err_actionable(
                            f"data[{code!r}] is a dict but contains no list of records",
                            received=records,
                            expected=(
                                f"data[{code!r}] = [{{'trade_date': '2023-12-11', "
                                "'close': 1544.555, ...}}, ...]"
                            ),
                            fix=(
                                "call get_market_data(codes=['600519.SH'], "
                                "start_date='2023-01-01', end_date='2023-12-31', "
                                "persist=True) to fetch and persist into DuckDB in one step"
                            ),
                            tool="import_data",
                        )
                    records = unwrapped
                    logger.debug("import_data: unwrapped data[%r]", code)

                rows = _persist_code_records(conn, code, records, strategy_name)
                total_rows += rows

            conn.close()
            return tool_ok({
                "imported": total_rows,
                "n_codes": len(data),
                "strategy_name": strategy_name,
                "message": f"Imported {total_rows} rows from {len(data)} codes into ohlcv table",
            })

        except Exception as exc:
            logger.exception("import_data failed")
            return err_actionable(
                f"import failed: {exc}",
                received=str(data)[:200],
                expected="dict[asset_code, list[record]] from get_market_data",
                fix="verify data shape and workspace is writable",
                tool="import_data",
            )


def _persist_code_records(conn, code: str, records, strategy_name: str) -> int:
    """Normalize + persist one code's records into price_data; returns rows written."""
    import pandas as pd

    if not records:
        return 0
    df = pd.DataFrame(records)
    if df.empty:
        return 0

    # Normalize column names
    col_map = {}
    for col in df.columns:
        cl = col.lower()
        if cl in ("trade_date", "tradedate", "datetime"):
            col_map[col] = "date"
        elif cl in ("code", "symbol", "ticker"):
            col_map[col] = "asset_code"
    if col_map:
        df = df.rename(columns=col_map)

    # Ensure required columns
    if "asset_code" not in df.columns:
        df["asset_code"] = code
    if "date" not in df.columns:
        raise ValueError(
            f"data[{code!r}] has no 'date' or 'trade_date' column; "
            f"columns={list(df.columns)}"
        )

    # Fill missing OHLCV columns
    for c in ("open", "high", "low", "close", "volume"):
        if c not in df.columns:
            df[c] = df["close"] if c != "volume" else 0.0

    df["strategy_name"] = strategy_name
    df = df[["strategy_name", "asset_code", "date", "open", "high", "low", "close", "volume"]]

    conn.execute("""
        INSERT OR REPLACE INTO price_data
        (strategy_name, asset_code, date, open, high, low, close, volume)
        SELECT strategy_name, asset_code, date, open, high, low, close, volume
        FROM df
    """)
    return len(df)


# ── 5. CheckDataTool ─────────────────────────────────────────────


class CheckDataTool(BaseTool):
    """检查策略数据就绪性（覆盖/孤儿/窗口/密度/质量/因子语法/行级清洗建议）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.0.0
    # 变更: 初版 (docs/run-backtest-data-gate.md)
    #
    # ## 用途
    # 只读检查策略名下 DuckDB 数据是否就绪可跑回测: 资产覆盖(C1)、
    # 孤儿资产(C2)、窗口/新鲜度(C3)、覆盖密度(C4)、数据质量(C5)、
    # 因子语法(C6)、行级质量(C7)。不修改任何数据——修复由你决策
    # （get_market_data / clean_data / 改 config）。
    #
    # ## 参数
    # - strategy_name: 策略名 (必填)
    # - source: 'config'(默认) 读 strategies/<name>/config.yaml 的
    #   codes/start/end; 'explicit' 用下方显式参数
    # - codes/start_date/end_date: source='explicit' 时的显式检查范围
    # - include_cleaning: 是否附加 C7 行级统计 (默认 True)
    #
    # ## 示例
    # {"strategy_name": "blue_chip_momentum"}
    # {"strategy_name": "x", "source": "explicit",
    #  "codes": ["600519.SH"], "start_date": "2023-01-01", "end_date": "2024-12-31"}
    #
    # ## 边界
    # 只读 (effects 为空); run_backtest 内置同款门禁 (C1~C6, 不含 C7);
    # C7 依赖 clean_data 的 dry_run 语义, 失败降级为 warn。
    #
    # ## 错误处理范式
    # - 缺 strategy_name → error + expected
    # - 无 DB → C0 fail
    # - 修复动作参考各 check 的 fix_hint
    #
    # ## 相关工具
    # clean_data: 行级清洗; get_market_data: 补数据; run_backtest: 门禁同源
    # ─────────────────────────────────────────────
    """

    name = "check_data"
    description = (
        "检查策略数据就绪性（覆盖/孤儿/窗口/密度/质量/因子语法/行级）; 只读。"
    )
    repeatable = True
    category = "数据"
    effects = frozenset()

    def execute(
        self,
        ctx: ToolContext,
        strategy_name: str,
        source: str = "config",
        codes: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        include_cleaning: bool = True,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="check_data",
            )
        if not strategy_name:
            return err_actionable(
                "strategy_name is required",
                received=strategy_name,
                expected="non-empty strategy name, e.g. 'momentum_20d'",
                fix="pass an existing strategy name",
                tool="check_data",
            )
        if source not in ("config", "explicit"):
            return err_actionable(
                f"invalid source: {source}",
                received=source,
                expected="one of: config, explicit",
                fix="use 'config' (read strategies/<name>/config.yaml) or 'explicit' (pass codes/dates)",
                tool="check_data",
            )

        workspace = ctx.workspace
        cfg = None
        if source == "config":
            base = ctx.strategy_dir if ctx.strategy_dir is not None else workspace / "strategies" / strategy_name
            cfg_path = base / "config.yaml"
            if cfg_path.exists():
                from strategy_research.apps.config_runner import load_yaml_config

                try:
                    cfg = load_yaml_config(cfg_path)
                except Exception:  # noqa: BLE001
                    cfg = None
            else:
                return err_actionable(
                    f"config not found: {cfg_path}",
                    received=strategy_name,
                    expected="strategies/<name>/config.yaml for source='config'",
                    fix="use source='explicit' with codes/start_date/end_date for strategies without config",
                    tool="check_data",
                )

        from strategy_research.apps.data_readiness import check_data_readiness

        try:
            report = check_data_readiness(
                workspace,
                strategy_name,
                cfg=cfg,
                codes=codes,
                start_date=start_date,
                end_date=end_date,
                include_cleaning=include_cleaning,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("check_data failed")
            return err_actionable(
                f"check failed: {exc}",
                received=strategy_name,
                tool="check_data",
            )

        return tool_ok({
            "strategy_name": strategy_name,
            "readiness": report.to_dict(),
            "hint": "修复动作参考 readiness.checks[*].fix_hint",
        })


# ── 6. register_data_tools ──────────────────────────────────────────


def register_data_tools(registry: ToolRegistry) -> None:
    """Register all data tools into a ToolRegistry."""
    for tool_cls in (
        GetMarketDataTool,
        ListDataSourcesTool,
        SearchSymbolTool,
        ImportDataTool,
        CheckDataTool,
    ):
        registry.register(tool_cls())
