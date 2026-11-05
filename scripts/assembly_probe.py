"""Current context assembly shape: who builds what, and where the seams are."""
from pathlib import Path
import re

root = Path("src/agentnodes")

print("==== loop_assembly.py (the assembly point)")
p = root / "core/agent/loop_assembly.py"
print(p.read_text(encoding="utf-8"))

print("==== where build_messages is called")
for p in root.rglob("*.py"):
    if "__pycache__" in str(p):
        continue
    t = p.read_text(encoding="utf-8", errors="ignore")
    for i, line in enumerate(t.splitlines(), 1):
        if "build_messages" in line or "build_system_prompt" in line:
            print(f"  {p.name}:{i}: {line.strip()[:100]}")
