# -*- coding: utf-8 -*-
"""score_layer_attribution — 分层归属评分（**缩小人工复核范围**用，不是裁决器）。

来源：``docs/design/layer-attribution-review-20261105.md`` §1.3 / §1.4。
那个评审用临时脚本跑出来的结论固化到这里，供下一轮复查复用。

**它不做什么**
- 不判定「归层对错」——层归属的主判据是 AST 门禁
  （``scripts/arch/layer_matrix.py`` + ``tests/test_layer_matrix.py``）。
- 不自动处置——输出只是**可疑排序**，最终结论必须人工复核每个被标红的模块。
- 不假设自己不漏——§1.4 记录过一次漏判（``metrics.py`` 的
  ``_sharpe`` / ``_sortino`` 全是「下划线前缀 + 单领域词」），
  规则 3 就是那次漏判的补丁，但**下一轮仍须人工扫一遍 L4 全清单**。

**为什么不能只数业务词密度**
``strategy`` 一词在两个语境里同名：``kernel/agent/strategy/`` 是
loop 策略模式（``ReActStrategy`` / ``MinimalStrategy``），
``foundation/utils/strategy_engine.py`` 是量化策略引擎。
v1 用词频打分 → 72 个可疑、假阳性过半；v2 改用 AST 提取的
**结构化领域标识符** → 33 个可疑，人工复核后 9 个真问题。

**三条判定规则**（按「硬事实 → 语义 → 辅助」排序）

| 规则 | 判据 | 权重 |
|---|---|---:|
| 跨层出边方向 | 上行边 = 确定错误（硬事实）| ×12 / 条 |
| 领域标识符 | 规则 1/2/3 命中的函数名与类名（语义）| L4 ×10+6/个 · L3 ×2+2/个 |
| 状态信号 | 持连接 / 锁 / 全局单例（硬事实）| L1 +15 |
| 业务词密度 | 歧义太大 | ×0（仅参考）|

**用法**

    python scripts/arch/score_layer_attribution.py
    python scripts/arch/score_layer_attribution.py --top 40
    python scripts/arch/score_layer_attribution.py --root testcase/golden --json

``--root`` 用于把评分器指向任意包根（例如 ``testcase/golden``），
模块名一律按 ``agentnodes.<相对路径>`` 拼，层归属仍复用 ``layer_matrix``
的唯一事实源。

**已知误报**（人工复核时应直接排除）

| 模块 | 命中 | 为什么是误报 |
|---|---|---|
| `kernel/agent/strategy/factory.py` | `StrategyFactory` | loop 策略工厂，非量化 |
| `kernel/agent/compact.py` | `get_compaction_metrics` | 上下文压缩指标，非业绩指标 |
| `kernel/agent/exec_registry.py` | `extract_metrics_from_upstream` | 执行指标，非业绩指标 |
| `kernel/agent/builtin_tools/display_tools.py` | `_load_metrics` | 展示用运行指标 |
| `kernel/scheduled_research/cli.py` | `_parse_metric_targets` | 调度器的指标选择 |

**已知漏报风险**（不要假设自动评分没有漏）

- 领域词表外的专有命名（自定义因子名、专有指标名）不会被命中。
- **L4 层仍必须人工扫全清单**——L4 是「零业务」红线，只有几十个文件，
  人工过目的成本远低于漏一个模块的代价。

命名规范见 scripts/README.md（arch/ = check_*/sync_*/audit_*/score_*）。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layer_matrix as lm  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

# ── 领域词表 ────────────────────────────────────────────────────────
#
# 分两档：
#   STRONG —— 量化/研究域专有词，单段匹配也认（``_sharpe`` 这类要抓）。
#   WEAK   —— 歧义词（market / metric / study），只在**动词+领域词**的
#             结构里认；``git.py`` 里的 "market" 是纯词频误报。
#
# 明确排除：``strategy``。它在两个语境里同名——
# ``kernel/agent/strategy/`` 是 loop 策略模式（``ReActStrategy``），
# ``foundation/utils/strategy_engine.py`` 是量化策略引擎。
# 词表层面排除后，规则 2 靠「领域词在首段 + 实体后缀」的结构约束
# 仍能认出 ``StrategyEngine``，而 ``execute_strategy`` / ``get_strategy``
# 这类 loop 策略命名不再误报。

STRONG_NOUNS = {
    "backtest", "ohlcv", "factor", "alpha", "portfolio",
    "sharpe", "sortino", "calmar", "drawdown", "turnover",
    "rebalance", "position", "trade", "equity", "pnl", "ic", "ir",
    "hurdle", "cost", "benchmark", "universe", "signal", "hypothesis",
    "experiment", "valuation", "submarket", "quote", "candle",
    "dividend", "slippage", "return", "vol", "var", "cvar", "win",
    "rate", "profit", "loss", "ratio", "monthly", "annual", "weight",
    "exposure", "bar", "symbol", "drawdowns", "returns", "equities",
}

WEAK_NOUNS = {"market", "metric", "metrics", "research", "study"}

# 规则 1 用（不含 strategy —— 见上方排除说明）
RULE1_NOUNS = (STRONG_NOUNS | WEAK_NOUNS) - {"strategy"}
# 规则 2 用（首段 + 实体后缀的结构已把歧义挡在外面，strategy 可以回来）
RULE2_NOUNS = STRONG_NOUNS | WEAK_NOUNS | {"strategy"}

VERBS = {
    "run", "detect", "save", "load", "calculate", "calc", "compute",
    "apply", "generate", "build", "create", "fetch", "get", "set",
    "update", "register", "list", "rebalance", "download", "ingest",
    "parse", "extract", "aggregate", "align", "resample", "evaluate",
    "score", "rank", "select", "filter", "normalize", "prepare",
    "simulate", "execute", "query", "read", "write", "store", "persist",
    "import", "export", "validate", "check", "make", "add", "remove",
    "delete", "find", "collect", "estimate", "adjust", "convert",
    "transform", "summarize", "plot", "report", "backtest", "optimize",
    "search", "sync", "dump", "push", "pull", "emit", "handle", "win",
    "split", "group", "sort", "count", "track", "watch", "poll",
    "fold", "reduce", "scan", "sweep", "clip", "scale", "balance",
    "hedge", "trade", "index", "map", "walk", "visit", "probe",
}

ENTITY_SUFFIXES = {
    "Config", "Result", "Engine", "Model", "Metrics", "Record",
    "Callback", "Callbacks", "Stats", "Snapshot", "Report", "Builder",
    "Registry", "Provider", "Store", "Error", "Exception", "Analysis",
    "Analyzer", "Calculator", "Detector", "Loader", "Manager",
    "Strategy", "Portfolio", "Factory", "Handler", "Collector",
    "Aggregator", "Monitor", "Tracker", "Set", "Map", "Table", "Schema",
    "Point", "Series", "Curve", "Values", "Data", "Spec", "Params",
}

# 词形还原：名词复数 / 第三人称单数
_IRREGULAR = {"indices": "index", "metrics": "metric", "data": "data"}


def _norm(token: str) -> str:
    t = token.lower()
    if t in _IRREGULAR:
        return _IRREGULAR[t]
    if t.endswith("ies") and len(t) > 4:
        return t[:-3] + "y"
    if t.endswith("es") and len(t) > 4 and t[-3] in "sxzh":
        return t[:-2]
    if t.endswith("s") and not t.endswith("ss") and len(t) > 3:
        return t[:-1]
    return t


def _words(name: str) -> list[str]:
    """把标识符拆成词：snake_case 与 CamelCase 都要拆。

    ``BacktestConfig`` → ``["Backtest", "Config"]``；
    ``_win_rate_and_stats`` → ``["win", "rate", "and", "stats"]``。
    """
    out: list[str] = []
    for chunk in name.split("_"):
        if not chunk:
            continue
        out.extend(re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", chunk))
    return out

# L1 状态信号（持连接 / 锁 / 全局单例）
STATE_SIGNALS = {
    "sqlite3.connect": "持 SQLite 连接",
    "threading.Lock": "持线程锁",
    "Queue(": "持队列",
    "self._conn": "持连接句柄",
    "threading.RLock": "持可重入锁",
    "asyncio.Queue": "持异步队列",
}


# ── 规则实现 ────────────────────────────────────────────────────────


def rule1_verb_noun(name: str) -> str | None:
    """规则 1：动词 + 领域名词（``run_backtest`` / ``fetch_market_data``）。

    动词与领域词**不必相邻**，只要同在一个名字里即可——
    ``calc_bars_per_year``（动词 calc + 领域词 bar）、
    ``win_rate_and_stats``（动词 win + 领域词 rate）都能命中。
    限定「同在一个名字里」是为了让 ``execute_strategy`` /
    ``get_strategy`` 这类 loop 策略命名不误报（strategy 已从规则 1 词表排除）。
    """
    segs = [_norm(s) for s in name.lower().split("_") if s]
    if len(segs) < 2:
        return None
    verb = next((s for s in segs if s in VERBS), None)
    if verb is None:
        return None
    noun = next((s for s in segs if s in RULE1_NOUNS), None)
    if noun is None:
        return None
    return f"动词+领域词 [{verb}…{noun}]"


def rule2_noun_suffix(name: str) -> str | None:
    """规则 2：领域名词 + 实体后缀（``BacktestResult`` / ``StrategyEngine``）。

    领域词必须在**首词**——``ReActStrategy`` / ``MinimalStrategy`` 的首词
    不是 strategy，因此不命中（这正是与量化策略引擎的区分点）；
    ``StrategyEngine`` 的首词是 strategy，命中。
    """
    words = _words(name)
    if len(words) < 2:
        # 单个词本身就是领域名词（``Position`` / ``TradeRecord`` 的 Position）
        if len(words) == 1 and _norm(words[0]) in STRONG_NOUNS:
            return f"领域词单名 [{words[0]}]"
        return None
    head, tail = words[0], words[-1]
    if tail not in ENTITY_SUFFIXES:
        return None
    if _norm(head) in RULE2_NOUNS:
        return f"领域词+实体后缀 [{head}{tail}]"
    return None


def rule3_underscore_word(name: str) -> str | None:
    """规则 3：``_`` 前缀 + 单个领域词（``_sharpe`` / ``_sortino``）。

    §1.4 记录的漏判补丁：这些名字既不是「动词+领域词」也不是
    「领域词+实体后缀」，v2 规则未命中，**但它们是纯量化代码**。
    只对 STRONG 词表生效——``_market`` / ``_metrics`` 之类太泛。
    """
    if not name.startswith("_"):
        return None
    body = name.lstrip("_").lower()
    if not body:
        return None
    if _norm(body) in STRONG_NOUNS:
        return f"下划线前缀+领域词 [{name}]"
    return None


RULES = (rule1_verb_noun, rule3_underscore_word, rule2_noun_suffix)


def domain_hits(tree: ast.AST) -> list[str]:
    """AST 提取的函数/类名里命中领域标识符规则的条目。"""
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for rule in RULES:
            tag = rule(node.name)
            if tag:
                hits.append(f"{node.name}  ← {tag}")
                break
    return hits


def state_hits(tree: ast.AST, source: str) -> list[str]:
    """持连接 / 锁 / 队列等状态信号（AST 优先，文本兜底）。"""
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = ""
            if isinstance(fn, ast.Attribute):
                parts = []
                cur = fn
                while isinstance(cur, ast.Attribute):
                    parts.append(cur.attr)
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    parts.append(cur.id)
                name = ".".join(reversed(parts))
            elif isinstance(fn, ast.Name):
                name = fn.id
            for sig, desc in STATE_SIGNALS.items():
                if sig.endswith("(") and name.endswith(sig[:-1]):
                    out.append(f"{name}() → {desc}")
                elif sig.startswith("self.") and name.endswith(sig.split(".")[-1]):
                    out.append(f"{name} → {desc}")
    if not out:
        for sig, desc in STATE_SIGNALS.items():
            if sig.startswith("self.") and sig in source:
                out.append(f"{sig} → {desc}")
    return sorted(set(out))


def upward_edges(path: Path, mod: str, is_pkg: bool) -> list[tuple[str, str]]:
    """本模块的上行 import（层号更高 → 本层不允许）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    src_layer = lm.layer_of(mod)
    if src_layer is None:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for target in lm.resolve_targets(node, mod, is_pkg):
            tgt = lm.resolve_layer(target)
            if tgt is None:
                continue
            if lm.ORDER.index(tgt) < lm.ORDER.index(src_layer):
                out.append((target, tgt))
    return out


def module_name_for(rel: Path) -> str:
    parts = [lm.PKG] + [p for p in rel.as_posix()[:-3].split("/") if p != "__init__"]
    return ".".join(parts)


def scan(root: Path) -> list[dict]:
    rows: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", "scripts", "graphify-out")]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            path = Path(dirpath) / fn
            rel = path.relative_to(root)
            mod = module_name_for(rel)
            layer = lm.resolve_layer(mod)
            if layer is None:
                continue
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (OSError, SyntaxError):
                continue

            dom = domain_hits(tree)
            ups = upward_edges(path, mod, fn == "__init__.py")
            state = state_hits(tree, source) if layer == "L1" else []

            score = 0
            reasons: list[str] = []
            if ups:
                score += 12 * len(ups)
                reasons.append(f"上行边 ×{len(ups)}: "
                               + ", ".join(f"{t}(→{l})" for t, l in ups[:4]))
            if dom:
                w = 16 if layer == "L4" else (4 if layer == "L3" else 0)
                score += w * len(dom)
                if w:
                    reasons.append(f"领域标识符 ×{len(dom)} (L{layer[-1]} 权重 {w})")
            if state:
                score += 15
                reasons.append("状态信号: " + "; ".join(state[:3]))

            if score:
                rows.append({
                    "module": mod,
                    "path": str(rel).replace("\\", "/"),
                    "layer": layer,
                    "score": score,
                    "domain_count": len(dom),
                    "upward": len(ups),
                    "state": state,
                    "domain_hits": dom[:6],
                    "reasons": reasons,
                })
    rows.sort(key=lambda r: (-r["score"], r["module"]))
    return rows


def main() -> int:
    # Windows 默认 GBK 控制台兜底（同 sync_architecture_diagram.py）：
    # 本脚本只输出 GBK 安全字符，但万一以后加了 emoji 也不该在
    # 「打完结果之后」崩掉并把退出码变成 1。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(ROOT / "src" / "agentnodes"),
                    help="包根目录（默认 src/agentnodes）")
    ap.add_argument("--top", type=int, default=30, help="显示前 N 条")
    ap.add_argument("--min-score", type=int, default=1, help="分数下限")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    rows = [r for r in scan(Path(args.root)) if r["score"] >= args.min_score]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print(f"分层归属评分 — {args.root}")
    print(f"可疑模块：{len(rows)}（仅缩小范围用，最终结论须人工复核）\n")
    if not rows:
        print("  （无）")
        return 0
    print(f"  {'分数':>5}  {'层':3}  {'领域':>4} {'上行':>4}  模块")
    print("  " + "-" * 78)
    for r in rows[: args.top]:
        print(f"  {r['score']:>5}  {r['layer']:3}  {r['domain_count']:>4} "
              f"{r['upward']:>4}  {r['path']}")
        for hit in r["domain_hits"][:3]:
            print(f"          · {hit}")
    if len(rows) > args.top:
        print(f"\n  …… 另有 {len(rows) - args.top} 条（--top 调整）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
