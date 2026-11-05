"""Probe context budget arbitration: who decides what fits in one LLM call."""
from pathlib import Path
import re

root = Path("src/agentnodes")

# 1. tool result truncation
p = root / "core/agent/tools.py"
t = p.read_text(encoding="utf-8")
for m in re.finditer(r"(MAX_[A-Z_]*|TRUNCAT[A-Z_]*|LIMIT[A-Z_]*)\s*=\s*([^\n]+)", t):
    print("tools.py const:", m.group(1), "=", m.group(2).strip())
for m in re.finditer(r"def (\w*trunc\w*|\w*limit\w*)\([^)]*\)", t):
    print("  trunc fn:", m.group(1))
print("mentions 'views':", [l.strip()[:90] for l in t.splitlines() if "views" in l][:6])

# 2. compaction triggers
p = root / "core/agent/strategy/steps/compaction.py"
if p.exists():
    t = p.read_text(encoding="utf-8")
    print("\n==== compaction.py")
    for l in t.splitlines():
        if any(k in l for k in ("threshold", "token", "def ", "compact")):
            print("  ", l.strip()[:100])

# 3. who calls compaction / where threshold_tokens is used
for rel in ["core/agent/loop.py", "core/agent/loop_compaction.py", "core/agent/chat_loop.py"]:
    p = root / rel
    if p.exists():
        t = p.read_text(encoding="utf-8")
        print(f"\n{rel}: threshold_tokens={t.count('threshold_tokens')} "
              f"estimate_tokens={t.count('estimate_tokens')} "
              f"validate={t.count('.validate(')} "
              f"token_budget={t.count('token_budget')}")

# 4. prompt builders: are the 4 implementations different in substance?
p = root / "core/agent/prompt_builder.py"
t = p.read_text(encoding="utf-8")
for cls in ["ChatPromptBuilder", "StaticFilePromptBuilder", "_NullBuilder"]:
    m = re.search(rf"^class {cls}\(.*?(?=^class |\Z)", t, re.M | re.S)
    if m:
        body = m.group(0)
        print(f"\n==== {cls} ({len(body.splitlines())} lines)")
        for line in body.splitlines():
            if line.strip().startswith("def ") or "return" in line and len(line) < 100:
                print("  ", line.strip()[:100])
