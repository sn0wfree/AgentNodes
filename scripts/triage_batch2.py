"""Extract batch-2 pyright errors (B: mechanical annotation alignment)."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

REPORT = Path("docs/pyright-current.json")
RULES = {
    "reportReturnType",
    "reportOperatorIssue",
    "reportOptionalSubscript",
    "reportOptionalIterable",
    "reportOptionalContextManager",
}


def main() -> None:
    data = json.loads(REPORT.read_text(encoding="utf-8-sig"))
    diags = data.get("generalDiagnostics", [])
    total = sum(1 for d in diags if d.get("severity") == "error")
    print(f"total errors: {total}")

    by_rule = Counter()
    batch2: list[dict] = []
    for d in diags:
        if d.get("severity") != "error":
            continue
        rule = d.get("rule") or ""
        by_rule[rule] += 1
        if rule in RULES:
            batch2.append(d)

    print("\nby rule (top):")
    for rule, n in by_rule.most_common():
        mark = " <- B" if rule in RULES else ""
        print(f"  {n:4d}  {rule}{mark}")

    print(f"\nbatch2 count: {len(batch2)}")

    by_file: dict[str, list[str]] = defaultdict(list)
    for d in batch2:
        f = d["range"]["start"]
        path = d.get("file", "?")
        rule = d.get("rule") or "?"
        msg = d.get("message", "").split("\n")[0][:120]
        by_file[path].append(f"  {f['line']+1}:{f['character']+1}  {rule}  {msg}")

    print(f"\nfiles: {len(by_file)}")
    for path in sorted(by_file, key=lambda p: -len(by_file[p])):
        print(f"\n{path}  ({len(by_file[path])})")
        for line in by_file[path][:8]:
            print(line)
        if len(by_file[path]) > 8:
            print(f"  ... +{len(by_file[path]) - 8} more")

    out = Path("docs/archive/undated/pyright-batch2-list.md")
    lines = [
        "# Pyright batch 2 — B 机械注解对齐清单",
        "",
        f"> 来源：`docs/pyright-current.json` · 规则：{sorted(RULES)}",
        f"> 总 errors {total} · 本批 {len(batch2)} · 文件 {len(by_file)}",
        "",
        "## 按文件",
        "",
    ]
    for path in sorted(by_file, key=lambda p: -len(by_file[p])):
        lines.append(f"### `{path}` ({len(by_file[path])})")
        lines.append("")
        lines.extend(by_file[path])
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
