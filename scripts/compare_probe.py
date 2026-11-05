"""Compare our repo against OpenClaw's structural patterns (evidence on our side)."""
from pathlib import Path
import re

root = Path("src/agentnodes")

print("════ 1. 'harness 可换' —— 我们有没有 runtime 选择层？")
for rel in ["core/agent/role_factory.py", "core/agent/chat_loop.py", "core/agent/loop_assembly.py"]:
    p = root / rel
    if not p.exists():
        continue
    t = p.read_text(encoding="utf-8", errors="ignore")
    print(f"\n-- {rel} ({len(t.splitlines())} lines)")
    for i, l in enumerate(t.splitlines(), 1):
        if re.search(r"def (build|assemble|resolve|create)\w*", l):
            print(f"   {i}: {l.strip()[:100]}")

print("\n════ 2. '一个调度器' —— 我们有几处调度？")
for p in root.rglob("*.py"):
    t = p.read_text(encoding="utf-8", errors="ignore")
    hits = [k for k in ("cron", "schedule", "APScheduler", "AsyncIOScheduler", "asyncio.sleep(") if k in t]
    if len(hits) >= 2 and t.count("def ") > 20:
        print(f"   {p.name:28s} {hits}  lines={len(t.splitlines())}")

print("\n════ 3. '写者栅栏' —— 我们有没有 superseded-run 防护？")
for kw in ("expected_writer", "active_writer", "writer_claim", "fence", "generation",
            "expected_writer_run_id", "state_version"):
    n = 0
    files = []
    for p in root.rglob("*.py"):
        t = p.read_text(encoding="utf-8", errors="ignore")
        if kw in t:
            n += t.count(kw)
            files.append(p.name)
    if n:
        print(f"   {kw:22s} {n:3d}  {files[:3]}")

print("\n════ 4. '协议版本化' —— 我们的协议有没有版本号？")
for kw in ("PROTOCOL_VERSION", "SCHEMA_VERSION", "schema_version", "user_version"):
    n = 0
    for p in root.rglob("*.py"):
        n += p.read_text(encoding="utf-8", errors="ignore").count(kw)
    print(f"   {kw:20s} {n}")

print("\n════ 5. '能力注册点' —— 我们的注册面有几处？")
for p in root.rglob("*.py"):
    t = p.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"^def register\(|^    def register\(", t, re.M):
        cls = re.findall(r"^class (\w+)", t, re.M)
        print(f"   {p.name:30s} classes={cls[:3]}")

print("\n════ 6. '记忆可归因清除' —— 我们的 memory 有 provenance / forget 吗？")
for kw in ("provenance", "forget", "retention", "purge", "delete_limit"):
    n = 0
    files = []
    for p in root.rglob("*.py"):
        t = p.read_text(encoding="utf-8", errors="ignore")
        if kw in t.lower():
            n += t.lower().count(kw)
            files.append(p.name)
    print(f"   {kw:14s} {n:3d}  {files[:3]}")
