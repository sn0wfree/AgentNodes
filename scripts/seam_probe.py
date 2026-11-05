"""Inventory: which integration seams are already protocols, which are concrete classes."""
from pathlib import Path
import re

root = Path("src/agentnodes")
files = [p for p in root.rglob("*.py") if "__pycache__" not in str(p) and "_archived" not in str(p)]

print("==== existing Protocols (seams already pluggable)")
protos = []
for p in files:
    t = p.read_text(encoding="utf-8", errors="ignore")
    for m in re.finditer(r"^class (\w+)\((?:[^)]*Protocol[^)]*)\):", t, re.M):
        protos.append((m.group(1), str(p).replace("\\", "/").split("agentnodes/")[-1]))
for name, f in sorted(protos):
    print(f"  {name:28s} {f}")

print("\n==== classes named *Provider/*Adapter/*Loader/*Store/*Registry (concrete?)")
for p in files:
    t = p.read_text(encoding="utf-8", errors="ignore")
    for m in re.finditer(r"^class (\w*(?:Provider|Adapter|Loader|Store|Registry|Source|Backend)\w*)\(([^)]*)\):", t, re.M):
        name, bases = m.group(1), m.group(2)
        kind = "PROTOCOL" if "Protocol" in bases else ("ABC" if "ABC" in bases else "concrete")
        print(f"  {name:32s} {kind:9s} {str(p).replace(chr(92),'/').split('agentnodes/')[-1]}")

print("\n==== registration / discovery mechanism today")
for rel in ["core/agent/registry.py", "core/agent/toolsets.py", "core/llm/__init__.py",
            "core/llm/provider/base.py", "core/storage/data_store_registry.py",
            "core/data_source/__init__.py", "core/mcp/server.py"]:
    q = root / rel
    if q.exists():
        t = q.read_text(encoding="utf-8", errors="ignore")
        print(f"\n-- {rel} ({len(t.splitlines())} lines)")
        print("   defs:", re.findall(r"^(?:    )?def (\w+)", t, re.M)[:12])
        for kw in ("register", "PROVIDERS", "TOOLSETS", "PLUGINS", "dict[str", "factory"):
            if kw in t:
                print("   has", kw)

print("\n==== is there a conformance/test-kit pattern today?")
for p in Path("tests").rglob("*.py"):
    t = p.read_text(encoding="utf-8", errors="ignore")
    if "Protocol" in t and ("conformance" in p.name.lower() or "contract" in p.name.lower()):
        print("  ", p.name)
print("  (searched conformance/contract test names)")
