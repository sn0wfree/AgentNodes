"""Audit autonomy surface: human gates vs agent decisions vs failure handling."""
from pathlib import Path
import re

root = Path("src/agentnodes")

print("==== 1. human gates (HITL / pause / approve / directive / objective)")
for rel in ["core/study/runner.py", "core/study/engine_common.py", "core/goal/store.py",
            "core/study/scheduler.py"]:
    p = root / rel
    if not p.exists():
        continue
    t = p.read_text(encoding="utf-8", errors="ignore")
    print(f"\n-- {rel}")
    for kw in ["hitl", "HITL", "pause", "approve", "interrupt", "directive", "await_approval",
               "human", "resume"]:
        n = t.count(kw)
        if n:
            print(f"   {kw:16s} {n}")

print("\n==== 2. agent autonomous decision points (AEGIS gates)")
for rel in ["core/study/runner_aegis.py", "core/study/phase_engine.py", "core/study/runner.py"]:
    p = root / rel
    if not p.exists():
        continue
    t = p.read_text(encoding="utf-8", errors="ignore")
    print(f"\n-- {rel}")
    for m in re.finditer(r"^    def (_?(?:check|should|decide|gate|reject|stop)\w*)", t, re.M):
        print("   ", m.group(1))
    for m in re.finditer(r"ShutdownReason\.(\w+)", t):
        pass
    reasons = sorted(set(re.findall(r"ShutdownReason\.(\w+)", t)))
    if reasons:
        print("    ShutdownReasons:", reasons)

print("\n==== 3. tools the agent can call (research pipeline coverage)")
p = root / "core/agent/toolsets.py"
if p.exists():
    t = p.read_text(encoding="utf-8")
    m = re.search(r"TOOLSETS: dict\[str, list\[str\]\] = \{(.*?)\n\}", t, re.S)
    if m:
        body = m.group(1)
        all_tools = sorted(set(re.findall(r'"([a-z_]+)"', body)))
        print("   all toolsets' tools:", len(all_tools))
        print("  ", all_tools)
    for name in ["researcher", "factor_analyst", "strategist", "data_quality"]:
        mm = re.search(rf'"{name}": \[(.*?)\]', t, re.S)
        if mm:
            print(f"   {name}: {re.findall(chr(34)+'([a-z_]+)'+chr(34), mm.group(1))}")

print("\n==== 4. failure handling: does the agent self-recover?")
for rel in ["core/agent/builtin_tools/backtest_tools.py", "core/agent/builtin_tools/factor_tools.py",
            "core/agent/builtin_tools/data_tools.py"]:
    p = root / rel
    if not p.exists():
        continue
    t = p.read_text(encoding="utf-8", errors="ignore")
    print(f"\n-- {rel}")
    for kw in ["err_actionable", "fix=", "ToolError", "retry", "fallback", "synthes", "degrade"]:
        n = t.count(kw)
        if n:
            print(f"   {kw:14s} {n}")
