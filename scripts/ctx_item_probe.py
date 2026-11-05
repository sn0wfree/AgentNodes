"""Read how context is actually produced today: injector output format + compaction input."""
from pathlib import Path
import re

root = Path("src/agentnodes")

p = root / "core/agent/context_injector.py"
t = p.read_text(encoding="utf-8", errors="ignore").splitlines()
print("==== injector output format (Goal + Todos + Memory)")
for a, b in [(105, 130), (140, 170), (175, 200)]:
    print(f"--- {a}-{b}")
    print("\n".join(f"{i+1}: {t[i]}" for i in range(a - 1, min(b, len(t)))))

print("\n==== block markers in use (the de-facto 'context item' format)")
for p in list((root / "core/agent").glob("*.py")) + list((root / "core/goal").glob("*.py")):
    t = p.read_text(encoding="utf-8", errors="ignore")
    marks = sorted(set(re.findall(r"<([a-z\-]{3,24})>", t)))
    if marks:
        print(f"  {p.name:26s} {marks}")

print("\n==== compaction expects: message dict shape")
p = root / "core/agent/compact.py"
t = p.read_text(encoding="utf-8", errors="ignore")
for m in re.finditer(r'def (_serialize_message|_serialize_tool_result|_serialize_assistant)\(.*?\n(?:.*\n){0,14}', t):
    print(m.group(0)[:700])
    print("   ...")
