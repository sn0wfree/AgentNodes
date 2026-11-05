"""Inject type-only field declarations onto mixin classes.

Adds a block like:

    # ── Host surface (provided by AgentLoop / AutoresearchRunner) ──
    workspace: Any
    _emit: Any

Never defines methods (would shadow the host at MRO). Only annotations.
Attributes already defined as methods/properties on the class are skipped.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

MIXINS = {
    "src/agentnodes/core/agent/loop_tools.py": "LoopToolsMixin",
    "src/agentnodes/core/agent/loop_progress.py": "LoopProgressMixin",
    "src/agentnodes/core/agent/loop_stream.py": "LoopStreamMixin",
    "src/agentnodes/core/agent/loop_compaction.py": "LoopCompactionMixin",
    "src/agentnodes/core/agent/loop_lifecycle.py": "LoopLifecycleMixin",
    "src/agentnodes/core/agent/loop_telemetry.py": "LoopTelemetryMixin",
    "src/agentnodes/core/study/runner_knowledge.py": "RunnerKnowledgeMixin",
    "src/agentnodes/core/study/runner_aegis.py": "RunnerAegisMixin",
}

MARK = "# ── Host surface (provided by AgentLoop / AutoresearchRunner) ──"


def class_body_attrs(node: ast.ClassDef) -> set[str]:
    defined: set[str] = set()
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(item.name)
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            defined.add(item.target.id)
        elif isinstance(item, ast.Assign):
            for t in item.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)
    return defined


def used_self_attrs(node: ast.ClassDef) -> set[str]:
    attrs: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id == "self":
            if not sub.attr.startswith("__"):
                attrs.add(sub.attr)
    return attrs


def process(path: Path, class_name: str) -> int:
    src = path.read_text(encoding="utf-8")
    if MARK in src:
        print(f"skip (already has block) {path}")
        return 0
    tree = ast.parse(src)
    target = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            target = node
            break
    if target is None:
        print(f"skip (class not found) {path} {class_name}")
        return 0

    defined = class_body_attrs(target)
    used = used_self_attrs(target)
    # also getattr(self, "x")
    for m in re.finditer(r'getattr\(self,\s*["\'](\w+)["\']', src):
        used.add(m.group(1))
    missing = sorted(used - defined - {"self"})
    if not missing:
        print(f"skip (nothing missing) {path}")
        return 0

    # insert after class docstring / first statement
    lines = src.splitlines(keepends=True)
    # class line is target.lineno (1-based); body starts at target.body[0].lineno
    insert_at = target.body[0].lineno  # 1-based line of first body stmt → insert before it
    # if first body is docstring, insert after it
    first = target.body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        insert_at = first.end_lineno or first.lineno
        # insert after docstring
        block = ["\n", MARK, "\n"]
        for a in missing:
            block.append(f"    {a}: Any\n")
        block.append("\n")
        lines[insert_at:insert_at] = block
    else:
        # insert before first body line
        indent_line = lines[insert_at - 1]
        # use 4-space class body indent
        block = [MARK + "\n"]
        for a in missing:
            block.append(f"    {a}: Any\n")
        block.append("\n")
        lines[insert_at - 1 : insert_at - 1] = block

    new_src = "".join(lines)
    if "from typing import" in new_src and "Any" not in new_src.split("from typing import", 1)[1].split("\n", 1)[0]:
        new_src = re.sub(
            r"from typing import ([^\n]+)",
            lambda m: f"from typing import Any, {m.group(1)}" if "Any" not in m.group(1) else m.group(0),
            new_src,
            count=1,
        )
    elif "from typing import Any" not in new_src:
        # add typing import after __future__
        new_src = new_src.replace(
            "from __future__ import annotations\n",
            "from __future__ import annotations\n\nfrom typing import Any\n",
            1,
        )

    path.write_text(new_src, encoding="utf-8")
    print(f"updated {path} +{len(missing)}: {', '.join(missing[:8])}{'...' if len(missing) > 8 else ''}")
    return len(missing)


def main() -> int:
    check = "--check" in sys.argv
    if check:
        for rel, cls in MIXINS.items():
            p = Path(rel)
            src = p.read_text(encoding="utf-8")
            tree = ast.parse(src)
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name == cls:
                    missing = sorted(used_self_attrs(node) - class_body_attrs(node))
                    print(f"{rel}: {len(missing)} -> {missing}")
        return 0
    total = 0
    for rel, cls in MIXINS.items():
        total += process(Path(rel), cls)
    print(f"total fields: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
