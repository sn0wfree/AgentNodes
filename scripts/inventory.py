"""Complexity inventory: what is the system made of, and where is mass concentrated."""
from pathlib import Path
import re
from collections import Counter

root = Path("src/agentnodes")
files = [p for p in root.rglob("*.py") if "__pycache__" not in str(p) and "_archived" not in str(p)]
tests = [p for p in Path("tests").rglob("test_*.py")]

print(f"src files {len(files)}  lines {sum(len(p.read_text(encoding='utf-8', errors='ignore').splitlines()) for p in files)}")
print(f"test files {len(tests)}")

print("\n==== core subsystem size (files / lines)")
subs = {}
for p in files:
    if "core" not in p.parts:
        continue
    i = p.parts.index("core")
    key = p.parts[i + 1] if len(p.parts) > i + 1 else "?"
    subs.setdefault(key, [0, 0])
    subs[key][0] += 1
    subs[key][1] += len(p.read_text(encoding="utf-8", errors="ignore").splitlines())
for k, (n, l) in sorted(subs.items(), key=lambda kv: -kv[1][1])[:16]:
    print(f"  {k:18s} {n:3d} files {l:6d} lines")

print("\n==== concentration: files > 800 lines")
big = []
for p in files:
    n = len(p.read_text(encoding="utf-8", errors="ignore").splitlines())
    if n > 800:
        big.append((n, p.name))
big.sort(reverse=True)
for n, name in big:
    print(f"  {n:5d}  {name}")
print(f"  total {len(big)} files, {sum(n for n,_ in big)} lines "
      f"({sum(n for n,_ in big) * 100 // max(1, sum(len(p.read_text(encoding='utf-8', errors='ignore').splitlines()) for p in files))}% of src)")

print("\n==== how many ways to change agent behaviour (extension surface)")
loop = (root / "core/agent/loop.py").read_text(encoding="utf-8")
print("  strategy steps:", len(list((root / "core/agent/strategy/steps").glob("*.py"))) - 1)
print("  loop mixins:", len([p for p in (root / "core/agent").glob("loop_*.py")]))
print("  hooks lifecycle points:", (root / "core/hooks/composite.py").read_text(encoding="utf-8").count("def ") if (root / "core/hooks/composite.py").exists() else "n/a")
print("  injectors:", len(re.findall(r"class \w+Injector", (root / "core/agent/context_injector.py").read_text(encoding="utf-8"))))
ts = (root / "core/agent/toolsets.py").read_text(encoding="utf-8")
print("  toolsets:", len(re.findall(r'^\s{4}"[a-z_]+": \[', ts, re.M)))
print("  builtin tools:", len(re.findall(r'^\s{4}name = "', "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in (root / "core/agent/builtin_tools").glob("*.py")), re.M)))

print("\n==== persistence surfaces (how many stores?)")
for p in files:
    if p.name in ("store.py", "db.py", "projection_store.py", "memory_manager.py", "persistent.py"):
        print("  ", p.name, len(p.read_text(encoding="utf-8", errors="ignore").splitlines()))
