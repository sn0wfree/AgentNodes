"""Probe agent-design surface: toolsets, injectors, context, orchestration, evals."""
from pathlib import Path
import re

root = Path("src/agentnodes")


def show(rel, *nums):
    p = root / rel if not rel.startswith("src") else Path(rel)
    if not p.exists():
        print("MISSING", rel)
        return
    t = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    print("====", rel, f"({len(t)} lines)")
    for n in nums:
        print(f"--{n}")
        print("\n".join(f"{i+1}: {t[i]}" for i in range(max(0, n - 6), min(len(t), n + 6))))


# 1. toolsets: how many, how selected
p = root / "core/agent/toolsets.py"
if p.exists():
    t = p.read_text(encoding="utf-8")
    names = re.findall(r'^\s*"([a-z_]+)":', t, re.M)
    print("toolsets defined:", len(names), names[:25])
    print("roles mentioned:", len(set(re.findall(r'role[=:\s]+"(\w+)"', t))))

# 2. injectors: how many, order semantics
p = root / "core/agent/context_injector.py"
if p.exists():
    t = p.read_text(encoding="utf-8")
    print("injector impls:", re.findall(r"class (\w+Injector)", t))

# 3. tools.py: result envelope / views
p = root / "core/agent/tools.py"
if p.exists():
    t = p.read_text(encoding="utf-8")
    print("ToolResult/views:", t.count("views"), "to_payload:", t.count("to_payload"))
    print("tool_errors wrapper:", "tool_errors" in t)

# 4. budget: is it enforced centrally
p = root / "core/agent/strategy/budget.py"
print("budget module exists:", p.exists())

# 5. orchestration systems
for rel, marker in [
    ("core/study/runner.py", "class AutoresearchRunner"),
    ("core/swarm/runtime.py", "class SwarmRuntime"),
    ("core/workflow/orchestrate_tool.py", "class "),
    ("core/goal/store.py", "class GoalStore"),
]:
    p = root / rel
    if p.exists():
        t = p.read_text(encoding="utf-8", errors="ignore")
        classes = re.findall(r"^class (\w+)", t, re.M)[:5]
        print(rel, ":", len(t.splitlines()), "lines, classes", classes)

# 6. evals
ev = Path("evals")
if ev.exists():
    files = list(ev.rglob("*.json")) + list(ev.rglob("*.yaml")) + list(ev.rglob("*.yml"))
    print("eval files:", len(files), [f.name for f in files[:12]])

# 7. how many places can change loop control flow
loop = (root / "core/agent/loop.py").read_text(encoding="utf-8")
print("loop.py continue/break/return in core:", len(re.findall(r"^\s+(continue|break|return)\b", loop, re.M)))
print("steps dir:", [p.name for p in (root / "core/agent/strategy/steps").glob("*.py")])
