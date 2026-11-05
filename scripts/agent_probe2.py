"""Probe tool surface, context assembly, orchestrator overlap, memory wiring."""
from pathlib import Path
import re

root = Path("src/agentnodes")

# 1. how many builtin tools exist, and how is a toolset selected for a run?
reg = root / "core/agent/registry.py"
if reg.exists():
    t = reg.read_text(encoding="utf-8")
    print("registry classes:", re.findall(r"^class (\w+)", t, re.M)[:8])
    print("registry funcs:", re.findall(r"^def (\w+)", t, re.M)[:12])

bt = root / "core/agent/builtin_tools"
if bt.exists():
    mods = [p for p in bt.glob("*.py") if not p.name.startswith("_")]
    print("builtin tool modules:", len(mods))
    total = 0
    for m in mods:
        t = m.read_text(encoding="utf-8", errors="ignore")
        total += len(re.findall(r"^\s{4}name = \"", t, re.M))
    print("approx builtin tools:", total)

# 2. where toolsets are chosen (role -> toolset)
for rel in ["core/agent/chat_loop.py", "core/agent/role_factory.py", "core/agent/loop_assembly.py"]:
    p = root / rel
    if p.exists():
        t = p.read_text(encoding="utf-8", errors="ignore")
        print(f"==== {rel} ({len(t.splitlines())} lines)")
        for m in re.finditer(r"toolset\w*", t):
            pass
        hits = sorted(set(re.findall(r'["\'](\w+_toolset|toolset)["\']', t)))
        print("toolset refs:", hits[:12])
        print("role refs:", sorted(set(re.findall(r'role\s*=\s*["\'](\w+)["\']', t)))[:12])

# 3. context assembly: system prompt + tools + history
for rel in ["core/agent/prompt_builder.py", "core/agent/loop_assembly.py"]:
    p = root / rel
    if p.exists():
        t = p.read_text(encoding="utf-8", errors="ignore")
        print(f"==== {rel} funcs:", re.findall(r"^def (\w+)|^    def (\w+)", t, re.M)[:20])
        for kw in ("tool_list", "system_prompt", "token", "truncat", "budget"):
            if kw in t:
                print("   mentions", kw, t.count(kw))

# 4. workflow vs swarm overlap
for rel in ["core/workflow/orchestrator.py", "core/workflow/store.py", "core/swarm/runtime.py"]:
    p = root / rel
    if p.exists():
        t = p.read_text(encoding="utf-8", errors="ignore")
        classes = re.findall(r"^class (\w+)", t, re.M)[:6]
        print(rel, ":", len(t.splitlines()), "lines, classes", classes)

# 5. memory retriever wiring (A4)
for rel in ["core/agent/context_injector.py", "core/agent/loop.py", "core/agent/strategy/steps/pre_run.py"]:
    p = root / rel
    if p.exists():
        t = p.read_text(encoding="utf-8", errors="ignore")
        print(f"{rel}: MemoryRetriever mentions={t.count('MemoryRetriever')}, "
              f"inject_pre_run calls={t.count('inject_pre_run')}")
