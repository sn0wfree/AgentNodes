"""Cluster reportArgumentType errors by file and callee signature pattern."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

data = json.loads(Path("docs/pyright-current.json").read_text(encoding="utf-8-sig"))
diags = [g for g in data["generalDiagnostics"] if g.get("severity") == "error"]
print("total", data["summary"]["errorCount"])
print("by rule:")
for r, n in Counter(g.get("rule") or "?" for g in diags).most_common():
    print(f"  {n:4d} {r}")

by_file: dict[str, list[str]] = defaultdict(list)
for g in diags:
    if g.get("rule") != "reportArgumentType":
        continue
    f = g["range"]["start"]
    path = g.get("file", "").replace("\\", "/").split("agentnodes/")[-1]
    msg = g.get("message", "").replace("\n", " | ")[:140]
    by_file[path].append(f"  {f['line']+1}:{f['character']+1}  {msg}")

print(f"\nfiles: {len(by_file)}  total: {sum(len(v) for v in by_file.values())}")
for path, lines in sorted(by_file.items(), key=lambda kv: -len(kv[1])):
    print(f"\n{path}  ({len(lines)})")
    for line in lines[:5]:
        print(line)
    if len(lines) > 5:
        print(f"  ... +{len(lines) - 5}")
