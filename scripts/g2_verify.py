"""Verify G2 claim: is MemoryRetrieverInjector actually wired and effective?"""
from pathlib import Path
import re

root = Path("src/agentnodes")

t = (root / "core/agent/context_injector.py").read_text(encoding="utf-8", errors="ignore")
lines = t.splitlines()
print("==== MemoryRetrieverInjector body (265-385)")
print("\n".join(f"{i+1}: {lines[i]}" for i in range(264, min(385, len(lines)))))

print("\n==== who calls build_default_injectors")
for p in list(root.rglob("*.py")) + list(Path("tests").rglob("*.py")):
    if "__pycache__" in str(p):
        continue
    tt = p.read_text(encoding="utf-8", errors="ignore")
    if "build_default_injectors" in tt or "MemoryRetrieverInjector" in tt:
        print("  ", p)

print("\n==== AgentLoop injector wiring")
lt = (root / "core/agent/loop.py").read_text(encoding="utf-8", errors="ignore")
for i, line in enumerate(lt.splitlines(), 1):
    if "injector" in line.lower():
        print(f"  {i}: {line.strip()[:120]}")
