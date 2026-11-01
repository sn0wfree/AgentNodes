"""覆盖率分档门禁。

为什么不用 `--cov-fail-under=90`：全库口径含归档死代码（1,485 条 0%）
与外部数据加载器，一刀切要么永远达不到，要么逼着给死代码写测试。
本脚本按「活跃核心」三档分别断言，口径见 docs/coverage-90-plan-20261030.md。

用法：
    pytest tests -q --cov=strategy_research --cov-report=term > cov.log 2>&1
    python scripts/cov_gate.py cov.log [--min 90]

退出码：0 达标 · 1 未达标 · 2 用法错误
"""
from __future__ import annotations

import argparse
import io
import re
import sys

# 档位定义：(名称, 路径前缀元组)
BANDS: dict[str, tuple[str, ...]] = {
    "A 平台内核": (
        "core/gateway/", "core/orchestration/", "core/runtimes/",
        "core/context/", "core/llm/", "core/storage/", "core/observability/",
    ),
    "B 研究域": ("core/study/", "core/goal/", "core/hypothesis"),
    "C 能力层": (
        "core/agent/", "core/memory/", "core/swarm/", "core/workflow/",
    ),
    # H：core/ 顶层的单文件研究工具（db / backtest / compute_factor /
    # factor_validate / data_readiness / data_import / config_runner）。
    # 它们没有子包前缀，前面的分档都匹配不到——2026-11-01 复核前
    # 3,966 行落在这条缝里，只 66%，而 db.py 有 18 处生产调用。
    # 前缀 "core/" 会命中所有子包，所以该档在聚合时跳过“已被前面档位
    # 消费”的行（见 aggregate()）。
    "H 研究工具核心": ("core/",),
}

# 永不进入分母的目录（与 .coveragerc 的 omit 对齐）
EXCLUDED = ("/_archived/",)

# 数据加载 / 回测引擎：外部数据源与重型数值计算，投产比低，
# 显式排除在门禁外（不假装它们被覆盖）。
EXTERNAL_HEAVY = (
    "core/data_source/", "core/backtest_engine/", "core/alpha_zoo/",
    "core/portfolio/", "core/export/", "core/validation/",
)


def parse_rows(log_path: str) -> list[tuple[str, int, int, int]]:
    lines = io.open(log_path, encoding="utf-8", errors="ignore").read().splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if "coverage: platform" in ln)
    except StopIteration:
        raise SystemExit("2: 日志中没有 coverage 段——请用 --cov 运行 pytest")

    rows: list[tuple[str, int, int, int]] = []
    for line in lines[start + 2:]:
        # 两种报告格式：
        #   term:         name  stmts  miss  cover%
        #   term-missing: name  stmts  miss  branch  brpart  cover%  missing
        m = re.match(
            r"^(\S.*?)\s+(\d+)\s+(\d+)\s+(?:\d+\s+\d+\s+)?([\d.]+)%", line
        )
        if not m or m.group(1).startswith("TOTAL"):
            continue
        p = m.group(1).replace("src\\strategy_research\\", "").replace("\\", "/")
        rows.append((p, int(m.group(2)), int(m.group(3)), int(float(m.group(4)))))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", help="pytest+coverage 输出文件")
    ap.add_argument("--min", type=float, default=90.0, help="每档最低覆盖率（默认 90）")
    args = ap.parse_args()

    rows = parse_rows(args.log)
    if not rows:
        print("2: 未解析到任何模块行", file=sys.stderr)
        return 2

    # 先归入窄档位（A/B/C），H 是「core/ 兜底」必须最后消费剩余行；
    # 数据加载/回测引擎显式排除，不计入任何档位。
    narrow = [n for n in BANDS if n != "H 研究工具核心"]
    agg = {name: [0, 0] for name in BANDS}
    for p, st, miss, _pct in rows:
        if any(x in p for x in EXCLUDED):
            continue
        if p.startswith(EXTERNAL_HEAVY):
            continue
        hit = next((n for n in narrow if p.startswith(BANDS[n])), None)
        if hit is None and p.startswith(BANDS["H 研究工具核心"]):
            hit = "H 研究工具核心"
        if hit is not None:
            agg[hit][0] += st
            agg[hit][1] += miss

    failed: list[str] = []
    print(f"{'档位':<16} {'语句':>7} {'未覆盖':>7} {'覆盖率':>8}  判定")
    for name in BANDS:
        st, miss = agg[name]
        if st == 0:
            print(f"{name:<16} {0:7d} {0:7d} {'n/a':>8}  跳过")
            continue
        pct = 100.0 * (st - miss) / st
        ok = pct >= args.min
        print(f"{name:<16} {st:7d} {miss:7d} {pct:7.1f}%  {'✅' if ok else '❌'}")
        if not ok:
            failed.append(f"{name} {pct:.1f}% < {args.min}%")

    total_st = sum(v[0] for v in agg.values())
    total_miss = sum(v[1] for v in agg.values())
    if total_st:
        print(
            f"\n门禁覆盖合计: {total_st} 语句 · {total_miss} 未覆盖 · "
            f"{100.0*(total_st-total_miss)/total_st:.1f}%"
        )

    if failed:
        print("\n未达标: " + "; ".join(failed), file=sys.stderr)
        return 1
    print(f"\n全部档位 ≥ {args.min}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
