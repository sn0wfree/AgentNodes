"""Integration cost probe: what does adding ONE component actually require today?"""
from pathlib import Path
import re

root = Path("src/agentnodes")

print("==== A. add a new LLM provider")
p = root / "core/llm/provider/__init__.py"
if p.exists():
    t = p.read_text(encoding="utf-8")
    print(t[:1800])

print("\n==== B. add a new data source")
p = root / "core/data_source/registry.py"
if p.exists():
    t = p.read_text(encoding="utf-8")
    print("defs:", re.findall(r"^def (\w+)", t, re.M))
    print(t[:1400])

print("\n==== C. add a new tool")
p = root / "core/agent/builtin_plugins.py"
if p.exists():
    t = p.read_text(encoding="utf-8")
    print("plugins file lines:", len(t.splitlines()))
    print("how tools are registered:", [l.strip()[:100] for l in t.splitlines() if "register" in l][:6])
p = root / "core/agent/registry.py"
print("registry is plugin-level, tools come from plugins")

print("\n==== D. capability discovery: can runtime ask 'who can do X'?")
for rel, kw in [("core/agent/registry.py", "by_category"),
                ("core/agent/toolsets.py", "resolve_toolset"),
                ("core/storage/data_store_registry.py", "available"),
                ("core/data_source/registry.py", "get_loader"),
                ("core/llm/provider/__init__.py", "PROVIDERS")]:
    q = root / rel
    if q.exists():
        t = q.read_text(encoding="utf-8", errors="ignore")
        print(f"  {rel}: {kw} present={kw in t}")
