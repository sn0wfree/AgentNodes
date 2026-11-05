"""覆盖率分档门禁。

为什么不用 `--cov-fail-under=90`：全库口径含归档死代码（1,485 条 0%）
与外部数据加载器，一刀切要么永远达不到，要么逼着给死代码写测试。
本脚本按四档分别断言，口径见 docs/archive/2026-10/coverage-90-plan-20261030.md。

四档：A 平台内核 / B 研究域 / C 能力层 / H 兜底。
2026-11-04 物理分层完成后路径改为层目录（W7-2）：
A=kernel 机制 + foundation 基础设施；B=apps 研究域三主包；
C=kernel 能力面四包；H=其余一切（apps 研究工具 + kernel 其余 +
foundation 其余 + interfaces），聚合时只消费窄档未认领的行。

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
        "kernel/gateway/", "kernel/orchestration/", "kernel/runtimes/",
        "kernel/context/", "foundation/llm/", "foundation/storage/",
        "foundation/observability/",
    ),
    "B 研究域": ("apps/study/", "apps/goal/", "apps/hypothesis"),
    "C 能力层": (
        "kernel/agent/", "kernel/memory/", "kernel/swarm/", "kernel/workflow/",
    ),
    # H：未被 A/B/C 认领的全部剩余——apps 研究工具（db/backtest/compute_factor/
    # factor_validate/data_*/config_runner/engine）、kernel 其余（session/
    # scheduled_research/events/permission/skills/hooks）、foundation 其余
    # （utils/web/errors/git/…）、interfaces。前缀 "" 匹配一切，
    # 本档在聚合时只消费「已被窄档认领」之外的行（见 aggregate 逻辑）。
    "H 兜底（其余全部）": ("",),
}

# 永不进入分母的目录（与 .coveragerc 的 omit 对齐）
EXCLUDED = ("/_archived/",)

# 数据加载 / 回测引擎：外部数据源与重型数值计算，投产比低，
# 显式排除在门禁外（不假装它们被覆盖）。
EXTERNAL_HEAVY = (
    "apps/data_source/", "apps/backtest_engine/", "apps/alpha_zoo/",
    "apps/portfolio/", "apps/export/", "apps/validation/",
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

    # 先归入窄档位（A/B/C），H 是「其余兜底」必须最后消费剩余行；
    # 数据加载/回测引擎显式排除，不计入任何档位。
    H = "H 兜底（其余全部）"
    narrow = [n for n in BANDS if n != H]
    agg = {name: [0, 0] for name in BANDS}
    for p, st, miss, _pct in rows:
        if any(x in p for x in EXCLUDED):
            continue
        if p.startswith(EXTERNAL_HEAVY):
            continue
        hit = next((n for n in narrow if p.startswith(BANDS[n])), None)
        if hit is None and p.startswith(BANDS[H]):
            hit = H
        if hit is not None:
            agg[hit][0] += st
            agg[hit][1] += miss

    failed: list[str] = []
    print(f"{'档位':<24} {'语句':>7} {'未覆盖':>7} {'覆盖率':>8}  判定")
    for name in BANDS:
        st, miss = agg[name]
        if st == 0:
            print(f"{name:<24} {0:7d} {0:7d} {'n/a':>8}  跳过")
            continue
        pct = 100.0 * (st - miss) / st
        ok = pct >= args.min
        print(f"{name:<24} {st:7d} {miss:7d} {pct:7.1f}%  {'✅' if ok else '❌'}")
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
