"""B1: wrap single-statement `return expr` in cast() for pandas-stub noise.

ONLY touches functions whose return annotation is pd.Series / Series / pd.DataFrame / DataFrame
and whose body is a single `return <expr>`. No multi-line transforms. No other edits.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

TARGETS = {
    "src/agentnodes/core/compute_factor.py",
    "src/agentnodes/core/alpha_zoo_ops.py",
    "src/agentnodes/core/data_source/akshare_loader.py",
    "src/agentnodes/core/tools/data_transforms.py",
    "src/agentnodes/core/config_runner.py",
    "src/agentnodes/core/data_source/fred_loader.py",
    "src/agentnodes/core/portfolio/cli.py",
}

SERIES_NAMES = {"Series", "pd.Series"}
DF_NAMES = {"DataFrame", "pd.DataFrame"}


def ann_name(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts = []
        n: ast.expr = node
        while isinstance(n, ast.Attribute):
            parts.append(n.attr)
            n = n.value
        if isinstance(n, ast.Name):
            parts.append(n.id)
        return ".".join(reversed(parts))
    return None


class Rewriter(ast.NodeTransformer):
    def __init__(self) -> None:
        self.rewrites: list[tuple[int, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef):  # noqa: N802
        self._maybe_rewrite(node)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):  # noqa: N802
        self._maybe_rewrite(node)
        return node

    def _maybe_rewrite(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        ann = ann_name(node.returns)
        if ann not in SERIES_NAMES and ann not in DF_NAMES:
            return
        body = node.body
        # allow a docstring first
        stmts = body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            stmts = body[1:]
        if len(stmts) != 1 or not isinstance(stmts[0], ast.Return) or stmts[0].value is None:
            return
        ret = stmts[0]
        assert ret.value is not None
        cast_target = "pd.Series" if ann in SERIES_NAMES else "pd.DataFrame"
        # build: cast(<target>, <orig>)
        new_expr = ast.Call(
            func=ast.Name(id="cast", ctx=ast.Load()),
            args=[
                ast.Name(id=cast_target, ctx=ast.Load()),
                ret.value,
            ],
            keywords=[],
        )
        ast.copy_location(new_expr, ret.value)
        ret.value = new_expr
        self.rewrites.append((ret.lineno, cast_target))


def process(path: Path) -> int:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    rw = Rewriter()
    rw.visit(tree)
    if not rw.rewrites:
        return 0
    # ensure typing.cast import
    if "from typing import cast" not in src and "from typing import (" not in src:
        # insert after the last of: __future__, stdlib, third-party — simplest: after first import block line containing "import"
        lines = src.splitlines(keepends=True)
        insert_at = 0
        for i, line in enumerate(lines):
            if line.startswith("import ") or line.startswith("from "):
                insert_at = i + 1
            elif insert_at and line.strip() and not line.startswith(("#", "import", "from", ")", " ", "\t", ")")):
                # first non-import content
                break
        # also handle `from __future__`
        for i, line in enumerate(lines):
            if line.startswith("from __future__"):
                insert_at = max(insert_at, i + 1)
        lines.insert(insert_at, "from typing import cast\n")
        src = "".join(lines)
    elif "cast" not in src.split("import", 1)[0] and "from typing import cast" not in src:
        # typing imported without cast — add cast to existing import if simple
        if "from typing import " in src:
            import re
            src2, n = re.subn(
                r"from typing import ([^\n(]+)",
                lambda m: f"from typing import cast, {m.group(1)}" if "cast" not in m.group(1) else m.group(0),
                src,
                count=1,
            )
            if n:
                src = src2
            else:
                src = src.replace("import typing", "import typing", 1)  # noop fallback
                # add standalone
                lines = src.splitlines(keepends=True)
                for i, line in enumerate(lines):
                    if line.startswith("from __future__"):
                        lines.insert(i + 1, "from typing import cast\n")
                        break
                src = "".join(lines)
        else:
            lines = src.splitlines(keepends=True)
            for i, line in enumerate(lines):
                if line.startswith("from __future__"):
                    lines.insert(i + 1, "from typing import cast\n")
                    break
            src = "".join(lines)

    # regenerate source for return statements via ast.unparse of full tree is too destructive.
    # Instead: textual replace only at known lineno for single-return functions.
    # Safer approach: reconstruct only the return lines.
    lines = src.splitlines(keepends=True)
    # re-parse to get current return value line numbers and original expr text via original source positions
    tree2 = ast.parse(path.read_text(encoding="utf-8"))
    rw2 = Rewriter()
    rw2.visit(tree2)
    # We already mutated nothing on disk for the return wrap — do text edit using original positions
    original = path.read_text(encoding="utf-8").splitlines(keepends=True)
    edits: list[tuple[int, int, int, int, str]] = []  # (line, col, endline, endcol, cast_target)
    for node in ast.walk(tree2):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        ann = ann_name(node.returns)
        if ann not in SERIES_NAMES and ann not in DF_NAMES:
            continue
        body = node.body
        stmts = body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            stmts = body[1:]
        if len(stmts) != 1 or not isinstance(stmts[0], ast.Return) or stmts[0].value is None:
            continue
        ret = stmts[0]
        cast_target = "pd.Series" if ann in SERIES_NAMES else "pd.DataFrame"
        v = ret.value
        edits.append((v.lineno, v.col_offset, v.end_lineno, v.end_lineno, cast_target, v))

    # Apply edits bottom-up on original lines using node segment
    new_src = path.read_text(encoding="utf-8")
    for v_lineno, v_col, v_end_line, _v_end, cast_target, v in sorted(edits, key=lambda e: -e[0]):
        # use ast.get_source_segment
        segment = ast.get_source_segment(new_src, v)
        if segment is None:
            print(f"  SKIP (no segment) {path}:{v_lineno}")
            continue
        replacement = f"cast({cast_target}, {segment})"
        # find exact occurrence at position — use absolute offset
        lines_all = new_src.splitlines(keepends=True)
        # compute offset
        offset = sum(len(lines_all[i]) for i in range(v_lineno - 1)) + v_col
        # careful: after prior edits offsets for earlier lines unchanged if we go bottom-up and same line not reused
        if new_src[offset : offset + len(segment)] != segment:
            # line-based fallback
            print(f"  SKIP (segment mismatch) {path}:{v_lineno} expected {segment[:40]!r}")
            continue
        new_src = new_src[:offset] + replacement + new_src[offset + len(segment) :]
        print(f"  wrap {path}:{v_lineno} -> cast({cast_target}, ...)")

    if new_src != path.read_text(encoding="utf-8"):
        # ensure cast import
        if "from typing import cast" not in new_src and "import cast" not in new_src:
            lines = new_src.splitlines(keepends=True)
            insert_at = 0
            for i, line in enumerate(lines):
                if line.startswith("from __future__"):
                    insert_at = i + 1
                elif line.startswith("import ") or line.startswith("from "):
                    insert_at = i + 1
            lines.insert(insert_at, "from typing import cast\n")
            new_src = "".join(lines)
        path.write_text(new_src, encoding="utf-8")
        print(f"WROTE {path} ({len(edits)} wraps)")
        return len(edits)
    return 0


def main() -> None:
    total = 0
    for rel in sorted(TARGETS):
        p = Path(rel)
        if not p.exists():
            print(f"missing {p}")
            continue
        total += process(p)
    print(f"total wraps: {total}")


if __name__ == "__main__":
    sys.exit(main())
