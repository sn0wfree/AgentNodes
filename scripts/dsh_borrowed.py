"""Is the DSH request-envelope pattern already borrowed here? And is there an invariant layer?"""
from pathlib import Path
import re

root = Path("src/agentnodes")

print("==== event_v2: request envelope / header")
p = root / "core/events/event_v2.py"
t = p.read_text(encoding="utf-8", errors="ignore")
for i, l in enumerate(t.splitlines(), 1):
    if any(k in l.lower() for k in ("envelope", "header", "request", "system", "tools", "epoch")):
        print(f"  {i}: {l.strip()[:110]}")
print("  event types:", re.findall(r'=\s*"([a-z_]+/[a-z_]+)"', t)[:25])

print("\n==== does any event record system prompt + tool schemas?")
for p in list((root / "core/events").glob("*.py")) + list((root / "core/agent").glob("*.py")):
    t = p.read_text(encoding="utf-8", errors="ignore")
    if "system_prompt" in t and ("tool" in t):
        hits = [l.strip()[:100] for l in t.splitlines() if "system_prompt" in l][:3]
        print(f"  {p.name}: {hits}")

print("\n==== invariant / guard layer today?")
for p in root.rglob("*.py"):
    t = p.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"def (check|assert|verify)_invariant|INVARIANT", t):
        print("  ", p.name, re.findall(r"def (check|assert|verify)_?\w*invariant\w*", t)[:3])
print("  (searched invariant-check helpers)")

print("\n==== guard pipeline (DSH-inspired ToolGuard)")
p = root / "core/agent/tools.py"
t = p.read_text(encoding="utf-8", errors="ignore")
i = t.find("class ToolGuard")
print(t[i:i+900] if i > 0 else "not found")
