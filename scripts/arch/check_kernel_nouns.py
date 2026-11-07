# -*- coding: utf-8 -*-
"""check_kernel_nouns — kernel 业务名词门禁。

**为什么存在**：三步走第一步的硬判据是「`kernel/` 不含任何业务域名词」。
在批次 4d 之前，这个判据只能靠人肉 review——而 review 会漏：
本轮实测就发现 8 处硬编码（权限枚举、校验器路由表、提示词路由、
fallback 计划、压缩预算表），全部是「哪天 review 的时候没想到」。

**判定范围（只扫代码，不扫注释与 docstring）**：

1. **字符串字面量**里的业务名词（``"run_backtest"`` / ``"ic_mean"`` ...）
2. **标识符**里的业务名词（``RUN_BACKTEST`` / ``_validate_backtest`` ...）
   —— 但只看「分词后的片段」，不做子串匹配（见 §词表）

**为什么不扫注释/docstring**：本仓库大量注释在**记录历史**
（「这批工具曾住在 kernel」「run_backtest 已删除」）。把它们判成违规，
门禁就会一片红、真实违规被淹没。设计文档 §5 已明文定此口径。

**为什么不用子串匹配**：``drawdown`` 是业务词，但 ``drawdown_analysis``
里的 ``analysis`` 是通用词；反过来 ``strategy`` 会命中 ``StrategyPattern``
这种模式名。所以本脚本：

- **标识符**：按 ``_`` 与驼峰切词，逐词比对（见 ``_split_identifier``）
- **字符串字面量**：按 ``_`` 切词，逐词比对

**豁免机制**（``EXEMPT``）：
- ``(file, lineno)`` 精确豁免（逐条登记，带理由）
- 整文件豁免（只给**数据/配置清单**类模块，不是代码模块）
- **不允许通配模块**——那会让门禁变成假的

**三种模式**：
1. 默认：发现即 exit 1，逐条打印 ``file:line`` + 命中的词 + 整行上下文
2. ``--report``：只打印统计，不判定
3. ``--selftest``：合成代码断言四类检测（标识符 / 字面量 / 注释豁免 / 切词）

用法：
    python scripts/arch/check_kernel_nouns.py             # 判定
    python scripts/arch/check_kernel_nouns.py --report    # 统计
    python scripts/arch/check_kernel_nouns.py --selftest  # 自检

命名规范见 scripts/README.md（arch/ = check_*，发现即失败）。
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
KERNEL = ROOT / "src" / "agentnodes" / "kernel"

#: 业务域名词词表（**小写**，按词比对）。
#:
#: **维护原则**（2026-11-06 首轮实测后的修正）：
#:
#: 1. 只收**领域专属且不会与通用概念撞词**的词
#: 2. 收一个词之前先想：kernel 里出现它，是不是**一定**意味着业务泄漏？
#: 3. 宁可少收（漏报一个，下次 review 还会碰见）；不可多收（误报会让人
#:    关掉门禁，那比没有门禁更糟）
#:
#: **``strategy`` 为什么不收**（实测 213 处误报）：
#: kernel 里 ``strategy`` 指的是 **LoopStrategy**（ReAct/Explorer/Validator
#: 三种执行策略）与 ``agent/strategy/`` 包名——**通用模式概念**，与量化
#: 的「交易策略」完全无关。同理 ``alpha``/``beta`` 是数学/统计通用词。
#:
#: 这类词要抓，只能靠**更长的复合词**（``trading_strategy`` /
#: ``strategy_name``）——但那样又会命中翻译字符串。**结论：不收。**
BUSINESS_NOUNS: frozenset[str] = frozenset({
    # 量化/金融核心（领域专属，不会与通用概念撞）
    "factor", "factors",
    "backtest", "backtests", "backtesting", "run_backtest",
    "portfolio", "portfolios",
    "ohlcv",
    "ticker", "tickers",
    "derivative", "derivatives",
    # 绩效/风险指标（量化专属）
    "sharpe", "calmar", "drawdown", "cvar",
    "quintile",
    # 因子分析（量化专属）
    "ic_mean", "ir_mean",
    "brinson",
    "anti_overfit",
    # 市场概念（限定为复合形式，避免 "marketplace"/"marketing"）
    "market_data", "get_market_data",
    "sector",
    # 业务角色名（复合形式，避免 "analyst" 撞通用）
    "factor_analyst", "risk_controller", "attribution_analyst",
    "anti_overfit_analyst", "data_quality", "portfolio_construction",
    "backtest_diagnostics",
})

#: 通用词白名单：分词后即使命中 ``BUSINESS_NOUNS`` 也放行。
GENERIC_WORDS: frozenset[str] = frozenset({
    "analysis", "analyze", "analyzer",
    "data", "document", "documents",
})

#: 复合词白名单：原始标识符/字面量含这些**完整片段**时不算违规。
#:
#: ``safety_factor`` / ``scale_factor`` / ``growth_factor`` 里的 ``factor``
#: 是**数学/工程通用词**（倍数、系数），与量化的「因子」无关。
#: 实测：kernel 里 15 处 ``*_factor`` 全是这个意思（token 估算的安全系数）。
GENERIC_COMPOUNDS: tuple[str, ...] = (
    "safety_factor",
    "scale_factor",
    "growth_factor",
    "load_factor",
    "decay_factor",
    "factor_of_safety",
)


def _is_generic_compound(raw: str) -> bool:
    """整串是否命中通用复合词白名单（命中则整串跳过）。"""
    low = raw.lower()
    return any(c in low for c in GENERIC_COMPOUNDS)

#: 精确豁免：``(相对路径, 行号) -> 理由``。
#:
#: **只允许逐条列**。每条必须说明为什么这一行可以有业务名词。
EXEMPT_LINES: dict[tuple[str, int], str] = {}

#: 整文件豁免：``相对路径 -> 理由``。
#:
#: 只给「数据/清单」类模块（它们的内容**就是**业务名词清单，
#: 而清单住在 L2；kernel 里若还有这种模块，说明数据还没迁走）。
EXEMPT_FILES: dict[str, str] = {}


@dataclass
class Hit:
    file: str
    line: int
    noun: str
    source: str      # "identifier" | "literal"
    context: str


def _split_identifier(name: str) -> list[str]:
    """把标识符切成小写词片段。

    ``RUN_BACKTEST``      → ``['run', 'backtest']``
    ``_validate_backtest`` → ``['validate', 'backtest']``
    ``DrawdownAnalysis``  → ``['drawdown', 'analysis']``
    ``anti_overfit``      → ``['anti', 'overfit']``
    """
    # 驼峰 → 下划线
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", spaced)
    return [p.lower() for p in re.split(r"[_\W]+", spaced) if p]


def _split_literal(text: str) -> list[str]:
    """字符串字面量按 ``_`` / 非字母数字切词（也切驼峰）。"""
    return _split_identifier(text)


def _check_words(words: list[str], nouns: frozenset[str]) -> list[str]:
    """返回命中的业务词（去重保序）。

    支持两种词表条目：
    - **单词**（``"backtest"``）：与切词结果逐词比对
    - **复合词**（``"factor_analyst"``）：与**原始标识符**子串比对
      （复合词表条目本身含 ``_``，切词后永远不可能逐词命中，所以走子串）

    后者需要调用方额外传 raw，见 ``_check_raw``。
    """
    seen: list[str] = []
    for w in words:
        if w in nouns and w not in seen:
            seen.append(w)
    return seen


def _check_raw(raw: str, words: list[str], nouns: frozenset[str]) -> list[str]:
    """单词按切词比对 + 复合词按整串比对（复合词以 ``_`` 为界）。

    先查通用复合词白名单（``safety_factor`` 等）——命中整串跳过。
    """
    if _is_generic_compound(raw):
        return []
    hits = _check_words(words, nouns)
    low = raw.lower()
    for noun in nouns:
        if "_" in noun and noun in low and noun not in hits:
            hits.append(noun)
    return hits


class _Visitor(ast.NodeVisitor):
    """收集字符串字面量与标识符。**不访问 docstring**。"""

    def __init__(self, nouns: frozenset[str]) -> None:
        self.nouns = nouns
        self.hits: list[tuple[int, str, str]] = []  # (lineno, noun, source)
        # docstring 行号（跳过这些行上的字符串字面量）
        self._docstring_lines: set[int] = set()

    def _record_docstrings(self, node: ast.AST) -> None:
        """把一个模块/类/函数的 docstring 行标记为跳过。"""
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            start = first.value.lineno
            end = getattr(first.value, "end_lineno", start)
            self._docstring_lines.update(range(start, end + 1))

    def visit_Module(self, node: ast.Module) -> None:
        self._record_docstrings(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record_docstrings(node)
        for w in _check_raw(node.name, _split_identifier(node.name), self.nouns):
            self.hits.append((node.lineno, w, "identifier"))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_docstrings(node)
        for w in _check_raw(node.name, _split_identifier(node.name), self.nouns):
            self.hits.append((node.lineno, w, "identifier"))
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Name(self, node: ast.Name) -> None:
        for w in _check_raw(node.id, _split_identifier(node.id), self.nouns):
            self.hits.append((node.lineno, w, "identifier"))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        for w in _check_raw(node.attr, _split_identifier(node.attr), self.nouns):
            self.hits.append((node.lineno, w, "identifier"))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # 跳过 docstring 行
        if node.lineno in self._docstring_lines:
            return
        if isinstance(node.value, str):
            for w in _check_raw(node.value, _split_literal(node.value), self.nouns):
                self.hits.append((node.lineno, w, "literal"))


def scan_file(
    path: Path, nouns: frozenset[str], *, base: Path | None = None
) -> list[Hit]:
    base = base or ROOT
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        print(f"[X] cannot parse {path}: {exc}", file=sys.stderr)
        return []
    visitor = _Visitor(nouns)
    visitor.visit(tree)
    lines = src.splitlines()
    out: list[Hit] = []
    for lineno, noun, source in visitor.hits:
        ctx = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
        try:
            rel = str(path.relative_to(base)).replace("\\", "/")
        except ValueError:
            rel = str(path).replace("\\", "/")
        out.append(Hit(
            file=rel,
            line=lineno,
            noun=noun,
            source=source,
            context=ctx[:110],
        ))
    return out


def scan_tree(
    root: Path | None = None,
    nouns: frozenset[str] | None = None,
    *,
    exempt_files: dict[str, str] | None = None,
    exempt_lines: dict[tuple[str, int], str] | None = None,
) -> list[Hit]:
    """扫描 kernel：``*.py``（AST）+ ``*.yaml/*.yml/*.json``（文本切词）
    + **目录名**（批次 8/1-F 扩展）。

    为什么扩展：批次 4d 的门禁只扫 ``.py``，``kernel/swarm/presets/``
    的 **37 个量化 YAML** 与 ``scheduled_research`` 的**目录名本身**
    全部漏网——「检查项不全被当成通过」的又一次发作。
    """
    root = root or KERNEL
    nouns = nouns if nouns is not None else BUSINESS_NOUNS
    exempt_files = exempt_files if exempt_files is not None else EXEMPT_FILES
    exempt_lines = exempt_lines if exempt_lines is not None else EXEMPT_LINES

    hits: list[Hit] = []
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts:
            continue
        if path.is_dir():
            continue
        suffix = path.suffix.lower()
        if suffix not in (".py", ".yaml", ".yml", ".json"):
            continue
        try:
            rel = str(path.relative_to(ROOT)).replace("\\", "/")
        except ValueError:
            # selftest 用的是临时目录，不在仓库下
            rel = str(path.relative_to(root)).replace("\\", "/")
        if rel in exempt_files:
            continue

        if suffix == ".py":
            file_hits = scan_file(path, nouns, base=root)
            source = "identifier"
        else:
            file_hits = scan_text_asset(path, nouns, base=root)
            source = "asset"

        for hit in file_hits:
            if (hit.file, hit.line) in exempt_lines:
                continue
            if source == "asset":
                hit.source = source
            hits.append(hit)

    # 目录名：切词后逐段比对（``scheduled_research`` 这类业务目录名
    # 在文件移走后仍会留下痕迹）
    hits.extend(scan_dir_names(root, nouns))
    return hits


def scan_text_asset(
    path: Path, nouns: frozenset[str], *, base: Path | None = None
) -> list[Hit]:
    """yaml/yml/json 资产：整文件按行切词比对（无 AST，无注释豁免——
    资产文件没有注释文化，全扫）。"""
    base = base or ROOT
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[Hit] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for w in _check_raw(line, _split_literal(line), nouns):
            try:
                rel = str(path.relative_to(base)).replace("\\", "/")
            except ValueError:
                rel = str(path).replace("\\", "/")
            out.append(Hit(
                file=rel, line=lineno, noun=w, source="asset",
                context=line.strip()[:110],
            ))
    return out


def scan_dir_names(
    root: Path | None = None, nouns: frozenset[str] | None = None
) -> list[Hit]:
    """kernel 下的目录名切词比对（相对 kernel 的每一段路径）。"""
    root = root or KERNEL
    nouns = nouns if nouns is not None else BUSINESS_NOUNS
    out: list[Hit] = []
    for path in sorted(root.rglob("*")):
        if not path.is_dir() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root)
        for part in rel.parts:
            for w in _check_raw(part, _split_identifier(part), nouns):
                out.append(Hit(
                    file="kernel/" + str(rel).replace("\\", "/"),
                    line=0,
                    noun=w,
                    source="dirname",
                    context=f"directory name segment {part!r}",
                ))
    return out


# ── selftest ─────────────────────────────────────────────────────────


def _selftest() -> int:
    import tempfile

    failures: list[str] = []
    nouns = frozenset({"backtest", "drawdown"})

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "ok_module.py").write_text(
            '"""Module docstring mentions backtest — must NOT be flagged."""\n'
            "\n"
            "def helper():\n"
            '    """Docstring with drawdown — also fine."""\n'
            "    # comment with backtest\n"
            "    return 1\n",
            encoding="utf-8",
        )
        (root / "bad_ident.py").write_text(
            "def _validate_backtest(x):\n"
            "    return x\n"
            "\n"
            "class DrawdownAnalysis:\n"
            "    pass\n",
            encoding="utf-8",
        )
        (root / "bad_literal.py").write_text(
            'TOOLS = ["read", "run_backtest"]\n'
            'MODE = "drawdown_analysis"\n',
            encoding="utf-8",
        )

        ok_hits = scan_tree(
            root, nouns, exempt_files={}, exempt_lines={}
        )
        files_hit = {h.file.split("/")[-1] for h in ok_hits}

        if "ok_module.py" in files_hit:
            failures.append("注释/docstring 被误报（ok_module 不该命中）")
        if "bad_ident.py" not in files_hit:
            failures.append("标识符里的业务词未命中（bad_ident）")
        if "bad_literal.py" not in files_hit:
            failures.append("字面量里的业务词未命中（bad_literal）")

        # 切词：DrawdownAnalysis 应切成 drawdown/analysis，命中 drawdown
        ident_nouns = {
            h.noun for h in ok_hits if h.file.endswith("bad_ident.py")
        }
        if "drawdown" not in ident_nouns:
            failures.append(f"驼峰切词失败：{ident_nouns}")

        # 豁免：整文件
        ex_hits = scan_tree(
            root, nouns,
            exempt_files={"bad_literal.py": "selftest"},
            exempt_lines={},
        )
        if any(h.file.endswith("bad_literal.py") for h in ex_hits):
            failures.append("整文件豁免未生效")

        # 豁免：精确到行
        literal_hit = next(
            h for h in ok_hits if h.file.endswith("bad_literal.py")
        )
        ex_hits2 = scan_tree(
            root, nouns,
            exempt_files={},
            exempt_lines={(literal_hit.file, literal_hit.line): "selftest"},
        )
        if any(
            h.file == literal_hit.file and h.line == literal_hit.line
            for h in ex_hits2
        ):
            failures.append("按行豁免未生效")

    if failures:
        print("[X] selftest FAILED")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("[OK] selftest passed (5 assertions)")
    return 0


# ── main ─────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", action="store_true", help="只统计，不判定")
    ap.add_argument("--selftest", action="store_true", help="校验器自检")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    hits = scan_tree()

    if args.report or hits:
        by_file: dict[str, list[Hit]] = {}
        for h in hits:
            by_file.setdefault(h.file, []).append(h)
        print(f"kernel 业务名词命中: {len(hits)} 处 / {len(by_file)} 文件")
        for f in sorted(by_file):
            print(f"\n  {f}")
            for h in by_file[f]:
                print(f"    {h.line:4d} [{h.source:10s}] {h.noun:14s} | {h.context}")

    if args.report:
        return 0
    if not hits:
        print("[OK] kernel nouns: clean")
        return 0
    print()
    print(f"[X] {len(hits)} business-noun hit(s) in kernel")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
