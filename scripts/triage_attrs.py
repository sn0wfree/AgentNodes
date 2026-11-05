"""Cluster reportAttributeAccessIssue by file + missing attribute name."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

data = json.loads(Path("docs/pyright-current.json").read_text(encoding="utf-8-sig"))
diags = [g for g in data["generalDiagnostics"] if g.get("severity") == "error"]
print("total", data["summary"]["errorCount"])
print("by rule:")
for r, n in Counter(g.get("rule") or "?" for g in diags).most_common():
    print(f"  {n:4d} {r}")

by_file: dict[str, list[str]] = defaultdict(list)
attrs: Counter[str] = Counter()
for g in diags:
    if g.get("rule") != "reportAttributeAccessIssue":
        continue
    f = g["range"]["start"]
    path = g.get("file", "").replace("\\", "/").split("agentnodes/")[-1]
    msg = g.get("message", "").replace("\n", " | ")[:160]
    m = re.search(r'attribute "([^"]+)"', msg)
    if m:
        attrs[m.group(1)] += 1
    by_file[path].append(f"  {f['line']+1}  {msg[:120]}")

print(f"\nfiles: {len(by_file)}  total: {sum(len(v) for v in by_file.values())}")
print("\ntop missing attrs:")
for a, n in attrs.most_common(25):
    print(f"  {n:4d}  {a}")

print("\nby file:")
for path, lines in sorted(by_file.items(), key=lambda kv: -len(kv[1]))[:20]:
    print(f"\n{path}  ({len(lines)})")
    for line in lines[:4]:
        print(line)
    if len(lines) > 4:
        print(f"  ... +{len(lines) - 4}")
