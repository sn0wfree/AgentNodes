"""List reportCallIssue samples for A-class triage."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

data = json.loads(Path("docs/pyright-current.json").read_text(encoding="utf-8-sig"))
diags = data["generalDiagnostics"]
print("total", data["summary"]["errorCount"])
c = Counter(g.get("rule") or "?" for g in diags if g.get("severity") == "error")
for r, n in c.most_common():
    print(f"{n:4d} {r}")

print("\n--- reportCallIssue ---")
for g in diags:
    if g.get("rule") != "reportCallIssue":
        continue
    f = g["range"]["start"]
    path = g.get("file", "").replace("\\", "/").split("agentnodes/")[-1]
    msg = g.get("message", "").replace("\n", " | ")[:130]
    print(f"{path}:{f['line']+1}  {msg}")
