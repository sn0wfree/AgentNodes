"""Triage reportOptionalMemberAccess: cluster by file, flag likely real None-risks."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

data = json.loads(Path("docs/pyright-current.json").read_text(encoding="utf-8-sig"))
diags = data["generalDiagnostics"]
print("total", data["summary"]["errorCount"])
c = Counter(g.get("rule") or "?" for g in diags if g.get("severity") == "error")
for r, n in c.most_common():
    print(f"{n:4d} {r}")

by_file: dict[str, list[str]] = defaultdict(list)
for g in diags:
    if g.get("severity") != "error":
        continue
    if g.get("rule") not in {
        "reportOptionalMemberAccess",
        "reportOptionalSubscript",
        "reportOptionalIterable",
        "reportOptionalContextManager",
    }:
        continue
    f = g["range"]["start"]
    path = g.get("file", "").replace("\\", "/").split("agentnodes/")[-1]
    msg = g.get("message", "").replace("\n", " | ")[:100]
    by_file[path].append(f"  {f['line']+1}:{f['character']+1}  {msg}")

print(f"\nOptional* files: {len(by_file)}  total: {sum(len(v) for v in by_file.values())}")
for path, lines in sorted(by_file.items(), key=lambda kv: -len(kv[1]))[:25]:
    print(f"\n{path}  ({len(lines)})")
    for line in lines[:6]:
        print(line)
    if len(lines) > 6:
        print(f"  ... +{len(lines) - 6}")
