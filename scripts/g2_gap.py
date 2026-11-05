"""Where is the real G2 gap: who writes memory, how is it injected, when."""
from pathlib import Path
import re

root = Path("src/agentnodes")
files = [p for p in root.rglob("*.py") if "__pycache__" not in str(p)]

print("==== PersistentMemory: writers vs readers")
for p in files:
    t = p.read_text(encoding="utf-8", errors="ignore")
    if "PersistentMemory" in t:
        writes = len(re.findall(r"\.(add|append|store|save|remember|upsert)\w*\(", t))
        reads = len(re.findall(r"\.(find_relevant|search|query|recall)\w*\(", t))
        print(f"  {str(p).replace(chr(92),'/').split('agentnodes/')[-1]:52s} writes={writes} reads={reads}")

print("\n==== persistent memory API")
p = root / "core/memory/persistent.py"
if p.exists():
    t = p.read_text(encoding="utf-8", errors="ignore")
    print("  classes:", re.findall(r"^class (\w+)", t, re.M))
    print("  defs:", re.findall(r"^    def (\w+)", t, re.M)[:20])
    print("  default path:", re.findall(r"DEFAULT\w*\s*=\s*[^\n]+", t)[:3])

print("\n==== agent-side memory write path (does the loop persist memories?)")
for rel in ["core/agent/loop.py", "core/agent/tools.py", "core/agent/loop_tools.py"]:
    q = root / rel
    if q.exists():
        t = q.read_text(encoding="utf-8", errors="ignore")
        print(f"  {rel}: remember={t.count('remember')} save_memory={t.count('save_memory')} "
              f"memory_manager={t.count('memory_manager')} add_memory={t.count('add_memory')}")

print("\n==== enable_memory_injection default")
for p in files:
    t = p.read_text(encoding="utf-8", errors="ignore")
    if "enable_memory_injection" in t:
        for i, line in enumerate(t.splitlines(), 1):
            if "enable_memory_injection" in line:
                print(f"  {p.name}:{i}: {line.strip()[:100]}")
