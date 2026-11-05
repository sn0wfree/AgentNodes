"""Probe tool visibility, prompt builder variants, workflow/swarm overlap."""
from pathlib import Path
import re

root = Path("src/agentnodes")

# 1. toolset -> tool filtering mechanism
p = root / "core/agent/toolsets.py"
t = p.read_text(encoding="utf-8")
print("==== toolsets.py head")
print("\n".join(t.splitlines()[:45]))
print("... funcs:", re.findall(r"^def (\w+)", t, re.M))
for m in re.finditer(r"def (\w+)\(([^)]*)\)[^:]*:\s*\n(?:.*\n){0,6}?.*?registry", t):
    pass
# find filtering
for kw in ("register", "filter", "allow", "enable", "subset"):
    for m in re.finditer(rf"def (\w+\([^)]*\)):[\s\S]{{0,400}}?{kw}", t):
        seg = m.group(0)[:300]
        if "def " in seg:
            print("---", seg.split("\n")[0], "| kw:", kw)
            break

# 2. prompt builder classes
p = root / "core/agent/prompt_builder.py"
t = p.read_text(encoding="utf-8")
print("\n==== prompt_builder classes:", re.findall(r"^class (\w+)", t, re.M))
for m in re.finditer(r"^class (\w+)\(([^)]*)\):", t, re.M):
    print("  ", m.group(1), "<-", m.group(2))
print("  defs per class:")
cur = None
for line in t.splitlines():
    mm = re.match(r"^class (\w+)", line)
    if mm:
        cur = mm.group(1)
        print("   ", cur, end=": ")
        continue
    dm = re.match(r"^    def (\w+)", line)
    if dm and cur:
        print(dm.group(1), end=" ")
print()

# 3. workflow vs swarm DAG
for rel in ["core/workflow/orchestrator.py", "core/workflow/dag.py", "core/swarm/runtime.py"]:
    p = root / rel
    if p.exists():
        t = p.read_text(encoding="utf-8")
        print(f"\n{rel}: {len(t.splitlines())} lines")
        print("   topological:", t.count("topological"), "layer:", t.count("layer"),
              "ThreadPool:", t.count("ThreadPool"), "AgentLoop:", t.count("AgentLoop"),
              "AgentExecutor:", t.count("AgentExecutor"))
