"""What can the system already modify about itself? (RSI feasibility probe)"""
from pathlib import Path
import re

root = Path("src/agentnodes")
repo = Path(".")

print("==== 1. mutable behavioural artifacts: prompts / roles")
for d in ["templates/.prompts", "templates/prompts", ".prompts"]:
    p = repo / d
    if p.exists():
        files = list(p.glob("*"))
        print(f"  {d}: {len(files)} files -> {[f.name for f in files[:12]]}")
prompts = list((repo / "templates").rglob("*.md"))
print(f"  templates/**/*.md: {len(prompts)}")
if prompts:
    print("   sample:", [str(p.relative_to(repo)) for p in prompts[:8]])

print("\n==== 2. can the agent write to them? (write tool + workspace git)")
for rel in ["core/agent/builtin_tools/file_tools.py", "core/agent/builtin_tools/git_tools.py" ]:
    q = root / rel
    if q.exists():
        t = q.read_text(encoding="utf-8", errors="ignore")
        tool_names = re.findall(r'name = "(\w+)"', t)[:6]
        print(f"  {rel}: {len(t.splitlines())} lines, tools: {tool_names}")
for p in root.rglob("*.py"):
    t = p.read_text(encoding="utf-8", errors="ignore")
    if "loop_evolution" in t or "def evolve" in t:
        print("  evolution module:", p.name, len(t.splitlines()), "lines")

print("\n==== 3. custom strategy steps (structural extension today)")
p = root / "core/agent/strategy/custom_steps.py"
if p.exists():
    t = p.read_text(encoding="utf-8")
    print("  custom_steps.py:", len(t.splitlines()), "lines, classes:", re.findall(r"^class (\w+)", t, re.M))

print("\n==== 4. fitness function: evals")
ev = repo / "evals"
if ev.exists():
    for f in sorted(ev.rglob("*")):
        if f.is_file():
            print(f"  {f} ({f.stat().st_size}b)")
cfg = ev / "config.yaml"
if cfg.exists():
    t = cfg.read_text(encoding="utf-8")
    print("  config head:", "\n   ".join(t.splitlines()[:18]))

print("\n==== 5. is there a held-out / private eval set?")
for p in list((repo / "evals").rglob("*")) + list(Path("tests").rglob("*.py")):
    if p.is_file() and p.suffix in (".yaml", ".json", ".py"):
        n = p.name.lower()
        if any(k in n for k in ("hold", "private", "hidden", "secret", "canary")):
            print("  ", p)
print("  (searched holdout/private/hidden/canary)")

print("\n==== 6. AEGIS anti-degeneracy gates")
for rel in ["core/study/runner_aegis.py", "core/study/phase_engine.py"]:
    q = root / rel
    if q.exists():
        t = q.read_text(encoding="utf-8", errors="ignore")
        print(f"  {rel}:")
        for m in re.finditer(r"^def (_?(?:novelty|regression|attribution)[_a-z]*)\(", t, re.M):
            print("     ", m.group(1))
        for kw in ("stagnation", "no_progress", "novel", "regress", "discard"):
            if kw in t:
                print(f"      mentions {kw}: {t.count(kw)}")
