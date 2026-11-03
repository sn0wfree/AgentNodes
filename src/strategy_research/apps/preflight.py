"""预检 (Pre-flight) 检查。

启动研究循环前确认基础设施可用：
- Critical（阻塞）：LLM key 至少一个存在
- Warning（警告）：DuckDB 可写、至少一个数据源可达
- Info（提示）：OHLCV 数据完整性

设计：返回 CheckResult 列表，不抛异常。调用方决定如何处理。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ============================================================
# 结果结构
# ============================================================

@dataclass(frozen=True)
class CheckResult:
    """单条检查结果。"""
    name: str           # 检查项名
    status: str         # 'ready' | 'error' | 'not_configured' | 'skipped'
    message: str        # 简短描述
    impact: str = ""    # 影响说明（失败时）
    critical: bool = False


# ============================================================
# 各项检查
# ============================================================

def _check_llm_provider() -> CheckResult:
    """检查 LLM key 至少一个存在。

    支持 OpenAI 兼容 (OPENAI_API_KEY / DEEPSEEK_API_KEY / KIMI_API_KEY / QWEN_API_KEY)。
    """
    candidates = [
        ("OPENAI_API_KEY", "OpenAI"),
        ("DEEPSEEK_API_KEY", "DeepSeek"),
        ("KIMI_API_KEY", "Kimi (Moonshot)"),
        ("QWEN_API_KEY", "Qwen (DashScope)"),
        ("ANTHROPIC_API_KEY", "Anthropic"),
    ]
    found = [(env, label) for env, label in candidates if os.environ.get(env)]

    if not found:
        return CheckResult(
            name="LLM Provider",
            status="error",
            message="未配置任何 LLM API key",
            impact="Agent 无法调用 LLM，研究循环无法启动。"
                  "请设置 OPENAI_API_KEY / DEEPSEEK_API_KEY / KIMI_API_KEY / QWEN_API_KEY 任一。",
            critical=True,
        )

    primary = found[0]
    return CheckResult(
        name="LLM Provider",
        status="ready",
        message=f"{primary[1]} (env {primary[0]})",
        critical=False,
    )


def _check_duckdb_writable(workspace_path: Path) -> CheckResult:
    """检查 DuckDB 可创建/写入测试表。"""
    try:
        import duckdb
    except ImportError:
        return CheckResult(
            name="DuckDB",
            status="not_configured",
            message="duckdb 包未安装",
            impact="pip install duckdb",
            critical=False,
        )

    db_path = workspace_path / "data.duckdb"
    try:
        conn = duckdb.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS _preflight_test (
                id INTEGER PRIMARY KEY,
                ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("INSERT INTO _preflight_test (id) VALUES (1) ON CONFLICT DO NOTHING")
        conn.execute("DROP TABLE _preflight_test")
        conn.close()
        return CheckResult(
            name="DuckDB",
            status="ready",
            message=f"writable: {db_path}",
        )
    except Exception as e:
        return CheckResult(
            name="DuckDB",
            status="error",
            message=f"写入失败: {e}",
            impact=f"无法连接或写入 {db_path}",
            critical=False,
        )


def _check_data_sources(workspace_path: Path) -> CheckResult:
    """检查至少一个数据源 is_available()=True。"""
    try:
        from strategy_research.core.data_source import LOADER_REGISTRY, list_loaders
        # 触发懒加载
        list_loaders()
    except Exception as e:
        return CheckResult(
            name="Data Sources",
            status="error",
            message=f"注册表加载失败: {e}",
            impact="无法导入 data_source 模块",
            critical=False,
        )

    available = []
    unavailable = []
    for name, cls in LOADER_REGISTRY.items():
        try:
            inst = cls()
            if inst.is_available():
                available.append(name)
            else:
                unavailable.append(name)
        except Exception:
            unavailable.append(name)

    if not available:
        return CheckResult(
            name="Data Sources",
            status="not_configured",
            message=f"无可用 loader ({len(unavailable)} 个不可用: {', '.join(unavailable[:5])})",
            impact="无法获取真实价格数据。请安装 akshare / 配置 TUSHARE_TOKEN / yfinance 等。",
            critical=False,
        )

    return CheckResult(
        name="Data Sources",
        status="ready",
        message=f"{len(available)} 个可用: {', '.join(available[:5])}",
    )


def _check_ohlcv_integrity(workspace_path: Path) -> CheckResult:
    """检查已导入的价格数据 OHLCV 完整性。"""
    try:
        from strategy_research.core.data_source import list_loaders
        list_loaders()  # trigger
    except Exception:
        pass

    try:
        import duckdb
        db_path = workspace_path / "data.duckdb"
        if not db_path.exists():
            return CheckResult(
                name="OHLCV Integrity",
                status="skipped",
                message="DuckDB 不存在，跳过",
            )

        conn = duckdb.connect(str(db_path), read_only=True)
        result = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN open IS NULL OR high IS NULL OR low IS NULL THEN 1 ELSE 0 END) as null_ohlc,
                SUM(CASE WHEN volume = 0 THEN 1 ELSE 0 END) as zero_vol
            FROM price_data
        """).fetchone()
        conn.close()

        total, null_ohlc, zero_vol = result or (0, 0, 0)

        if total == 0:
            return CheckResult(
                name="OHLCV Integrity",
                status="skipped",
                message="无 price_data 数据",
            )

        # 检查 OHLCV 是否被退化为 close-only
        if null_ohlc == total:
            return CheckResult(
                name="OHLCV Integrity",
                status="error",
                message=f"OHLC 全部为 NULL ({total} 行)，疑似被退化为 close-only",
                impact="回测无法用真实 OHLCV。请重新导入数据。",
            )

        return CheckResult(
            name="OHLCV Integrity",
            status="ready",
            message=f"{total} 行, OHLC 完整 {total - null_ohlc}/{total}",
        )
    except Exception as e:
        return CheckResult(
            name="OHLCV Integrity",
            status="error",
            message=f"检查失败: {e}",
            critical=False,
        )


# ============================================================
# 总入口
# ============================================================

def run_preflight(workspace_path: Optional[Path] = None,
                   verbose: bool = True) -> list[CheckResult]:
    """运行所有预检。返回 CheckResult 列表。

    Critical 检查失败时，调用方应阻止启动。
    """
    if workspace_path is None:
        workspace_path = Path.cwd()

    checks: list[CheckResult] = [
        _check_llm_provider(),
        _check_duckdb_writable(workspace_path),
        _check_data_sources(workspace_path),
        _check_ohlcv_integrity(workspace_path),
    ]

    if verbose:
        _print_results(checks)

    return checks


def _print_results(results: list[CheckResult]) -> None:
    """Rich 表样式输出（不依赖 rich 包，纯 ASCII）。"""
    status_mark = {
        "ready": "[OK]",
        "error": "[FAIL]",
        "not_configured": "[N/A]",
        "skipped": "[SKIP]",
    }
    color = {
        "ready": "\033[32m",       # green
        "error": "\033[31m",       # red
        "not_configured": "\033[33m",  # yellow
        "skipped": "\033[90m",     # gray
    }
    reset = "\033[0m"
    use_color = sys.stdout.isatty()

    print("\n" + "=" * 70)
    print("  quantnodes-research Pre-flight Check")
    print("=" * 70)

    for r in results:
        mark = status_mark.get(r.status, r.status)
        c = color.get(r.status, "") if use_color else ""
        e = reset if use_color else ""
        critical_tag = " [CRITICAL]" if r.critical else ""
        print(f"  {c}{mark:<8}{e} {r.name:<20}{critical_tag}")
        print(f"           {r.message}")
        if r.status != "ready" and r.impact:
            print(f"           → {r.impact}")

    critical_failed = [r for r in results if r.critical and r.status != "ready"]
    print("=" * 70)
    if critical_failed:
        print(f"  ❌ {len(critical_failed)} 项 CRITICAL 检查失败，agent 无法启动")
    else:
        ready_count = sum(1 for r in results if r.status == "ready")
        print(f"  ✓ {ready_count}/{len(results)} 项 ready")
    print()
