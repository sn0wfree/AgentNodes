from pathlib import Path
import re

t = Path("src/agentnodes/api/session/service.py").read_text(encoding="utf-8")
methods = re.findall(r"def (\w+)", t)
print("SessionService methods", len(methods))
print(methods)

t2 = Path("src/agentnodes/api/routers/study.py").read_text(encoding="utf-8")
print("study routes:")
for m in re.finditer(r'@router\.(get|post|put|delete|patch)\("([^"]+)"', t2):
    print(" ", m.group(1), m.group(2))

# core->api violation details
for rel in [
    "src/agentnodes/core/agent/event_store.py",
    "src/agentnodes/core/study/scheduler.py",
]:
    text = Path(rel).read_text(encoding="utf-8")
    for i, line in enumerate(text.splitlines(), 1):
        if "api" in line and ("import" in line or "from" in line):
            print(rel, i, line.strip()[:120])
