"""Deep dive: orchestration layer + engine layer structure for layer-by-layer discussion."""
from pathlib import Path
import re

root = Path("src/agentnodes")

print("════ 编排层：AgentLoop 的两条拆解轴（mixin vs step）是否重叠")
mix_dir = root / "core/agent"
for p in sorted(mix_dir.glob("loop_*.py")):
    t = p.read_text(encoding="utf-8", errors="ignore")
    defs = re.findall(r"^    def (\w+)|^    async def (\w+)", t, re.M)
    names = [a or b for a, b in defs]
    print(f"\n-- {p.name} ({len(t.splitlines())} lines)")
    print("   ", ", ".join(names[:14]))

step_dir = root / "core/agent/strategy/steps"
print("\n════ steps")
for p in sorted(step_dir.glob("*.py")):
    if p.name.startswith("__"):
        continue
    t = p.read_text(encoding="utf-8", errors="ignore")
    defs = re.findall(r"^    def (\w+)|^    async def (\w+)", t, re.M)
    names = [a or b for a, b in defs]
    print(f"-- {p.name}: {', '.join(names[:8])}")

print("\n════ 编排层：四个编排系统各自的规模与入口")
for rel in ["core/study/runner.py", "core/swarm/runtime.py", "core/workflow/orchestrate_tool.py",
            "core/goal/store.py", "core/agent/loop.py", "api/session/service.py"]:
    p = root / rel
    if not p.exists():
        print("  MISSING", rel)
        continue
    t = p.read_text(encoding="utf-8", errors="ignore")
    classes = re.findall(r"^class (\w+)", t, re.M)
    print(f"  {rel}: {len(t.splitlines())} lines, classes={classes[:4]}")

print("\n════ 引擎层：公共契约")
for rel in ["core/study/phase_engine.py"]:
    p = root / rel
    t = p.read_text(encoding="utf-8", errors="ignore")
    print(f"\n-- {rel} ({len(t.splitlines())} lines)")
    print("   defs:", re.findall(r"^def (\w+)", t, re.M)[:12])
    print("   classes:", re.findall(r"^class (\w+)", t, re.M)[:8])
    for kw in ("RoundOutcome", "BranchContext", "HITL", "interrupt", "checkpoint", "novelty",
               "claims", "journal", "review_cycle"):
        if kw in t:
            print(f"     {kw}: {t.count(kw)}")

print("\n════ 引擎层：谁在用哪个引擎")
for eng in ("phase_engine",):
    users = []
    for p in root.rglob("*.py"):
        t = p.read_text(encoding="utf-8", errors="ignore")
        if eng in t:
            users.append(p.name)
    print(f"  {eng}: {len(users)} files -> {users[:9]}")
