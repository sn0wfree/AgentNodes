"""What is DSH here, and what has the existing plugin-ization actually bought us?"""
from pathlib import Path
import re

root = Path("src/agentnodes")

print("==== DSH references (docs + code)")
for p in list(Path("docs").glob("*.md")) + list(root.rglob("*.py")) + list(Path("tests").glob("*.py")):
    t = p.read_text(encoding="utf-8", errors="ignore")
    if "DSH" in t or "dsh" in p.name.lower():
        hits = [l.strip()[:110] for l in t.splitlines() if "DSH" in l][:3]
        print(f"  {p}: {hits}")

print("\n==== AgentPlugin: what IS a plugin here?")
p = root / "core/agent/plugin.py"
print(p.read_text(encoding="utf-8")[:2200] if p.exists() else "MISSING")

print("\n==== who consumes AgentPluginRegistry")
for p in root.rglob("*.py"):
    t = p.read_text(encoding="utf-8", errors="ignore")
    if "AgentPluginRegistry" in t or "get_default_registry" in t:
        n = len(re.findall(r"get_default_registry\(", t))
        print(f"  {p.name}: {n} call sites")
