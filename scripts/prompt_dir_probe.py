from pathlib import Path
import re

for p in Path("src/agentnodes").rglob("*.py"):
    t = p.read_text(encoding="utf-8", errors="ignore")
    for m in re.finditer(r"[\"']([^\"']*\.prompts[^\"']*)[\"']", t):
        print(p.name, "->", m.group(1))

print("--- role prompt files on disk")
for pat in ("templates/**/*.md", "src/agentnodes/templates/**/*.md", "**/.prompts/*.md"):
    fs = [str(p) for p in Path(".").glob(pat)][:12]
    if fs:
        print(pat, len(list(Path(".").glob(pat))), fs[:6])

print("--- role_factory prompt dir")
p = Path("src/agentnodes/core/agent/role_factory.py")
if p.exists():
    t = p.read_text(encoding="utf-8")
    for i, l in enumerate(t.splitlines(), 1):
        if "prompt" in l.lower() and ("path" in l.lower() or "dir" in l.lower() or "/" in l):
            print(f"  {i}: {l.strip()[:110]}")
