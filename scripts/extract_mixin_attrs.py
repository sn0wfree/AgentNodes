"""Extract self.<attr> usages from mixin classes for field-declaration blocks."""
from __future__ import annotations

import ast
import re
from pathlib import Path

MIXINS = [
    "src/agentnodes/core/agent/loop_tools.py",
    "src/agentnodes/core/agent/loop_progress.py",
    "src/agentnodes/core/agent/loop_stream.py",
    "src/agentnodes/core/agent/loop_compaction.py",
    "src/agentnodes/core/agent/loop_lifecycle.py",
    "src/agentnodes/core/agent/loop_telemetry.py",
    "src/agentnodes/core/study/runner_knowledge.py",
    "src/agentnodes/core/study/runner_aegis.py",
]


def self_attrs(path: Path) -> tuple[str, list[str]]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    class_name = "?"
    attrs: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.endswith("Mixin"):
            class_name = node.name
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id == "self":
                    if not sub.attr.startswith("__"):
                        attrs.add(sub.attr)
    # also catch getattr(self, "x"
    for m in re.finditer(r'getattr\(self,\s*["\'](\w+)["\']', src):
        attrs.add(m.group(1))
    return class_name, sorted(attrs)


def main() -> None:
    for rel in MIXINS:
        p = Path(rel)
        if not p.exists():
            print("missing", rel)
            continue
        name, attrs = self_attrs(p)
        print(f"\n{name}  ({len(attrs)})")
        print("  " + ", ".join(attrs))


if __name__ == "__main__":
    main()
