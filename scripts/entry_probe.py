from pathlib import Path
import re

root = Path("src/agentnodes")

print("════ 我们现在有多少种「启动一次工作」的入口")
entries = []
for p in root.rglob("*.py"):
    if "_archived" in str(p) or "__pycache__" in str(p):
        continue
    t = p.read_text(encoding="utf-8", errors="ignore")
    # AgentLoop instantiation sites
    for m in re.finditer(r"AgentLoop\(", t):
        entries.append(("AgentLoop", str(p).replace("\\", "/").split("agentnodes/")[-1]))
    for m in re.finditer(r"AutoresearchRunner\(", t):
        entries.append(("AutoresearchRunner", str(p).replace("\\", "/").split("agentnodes/")[-1]))
    for m in re.finditer(r"SwarmRuntime\(", t):
        entries.append(("SwarmRuntime", str(p).replace("\\", "/").split("agentnodes/")[-1]))
    for m in re.finditer(r"AgentExecutor\(", t):
        entries.append(("AgentExecutor", str(p).replace("\\", "/").split("agentnodes/")[-1]))
    for m in re.finditer(r"SessionService\(", t):
        entries.append(("SessionService", str(p).replace("\\", "/").split("agentnodes/")[-1]))

from collections import Counter
c = Counter(e[0] for e in entries)
print("实例化点计数：")
for k, n in c.most_common():
    print(f"  {k:22s} {n:3d} 处")
print("\n文件级明细（前 20）：")
seen = set()
for kind, f in entries:
    key = f"{kind}@{f}"
    if key not in seen:
        seen.add(key)
        print(f"  {kind:22s} {f}")
        if len(seen) >= 20:
            break

print("\n════ 入口背后的「启动方式」有几种？")
# SessionService.send_message → asyncio.Queue + create_task
# StudyRunner.run → asyncio loop
# SwarmRuntime.execute → ThreadPool
# AgentExecutor.execute → AgentLoop.run
# cli main → 直接调函数
print("""
  入口           启动方式             并发控制            结果获取        事件出口
  ─────────────────────────────────────────────────────────────────────────────────────
  CLI cmd_run      直接调函数            无（同步）           返回值          print
  CLI chat         AgentLoop.run()       无                  LoopResult      print / trace
  FastAPI /send    SessionService        asyncio.Queue       dict (poll)     event_bus.emit
                    → create_task
  FastAPI /study   bootstrap→scheduler   Semaphore           poll study 状态  event_bus.emit
  FastAPI SSE      event_bus → SSE       —                   流              SSE
  MCP tool         AgentExecutor         无                  str             —
  cron/automation  scheduler._loop       Semaphore           poll            event_bus.emit
  subagent tool    AgentExecutor         无                  str             —
""")
