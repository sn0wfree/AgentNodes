"""Map today's ownership: admission(谁跑) / intent(跑什么) / environment(什么条件)."""
from pathlib import Path
import re

root = Path("src/agentnodes")

def show(rel, pats):
    p = root / rel
    if not p.exists():
        print("MISSING", rel); return
    t = p.read_text(encoding="utf-8", errors="ignore")
    print(f"\n══ {rel} ({len(t.splitlines())} lines)")
    for i, l in enumerate(t.splitlines(), 1):
        if any(re.search(pat, l) for pat in pats):
            print(f"  {i}: {l.strip()[:105]}")

print("──── 准入（谁下一个跑 / 何时跑）")
show("api/session/service.py", [r"def (send_message|cancel|resume_queue|_process_session_queue|is_session_processing)", r"Queue", r"asyncio\.Queue", r"create_task"])
show("core/study/scheduler.py", [r"^class ", r"def (schedule|_loop|run_once|acquire|release)", r"Semaphore", r"max_", r"concurren"])
show("core/study/runner.py", [r"def (_top_control_check|_check_stop_conditions|_finalize_round)", r"ControlToken", r"pause", r"resume"])

print("\n──── 意图（跑什么：目标/假设/指令）")
show("core/study/phase_engine.py", [r"class (RoundOutcome|BranchContext)", r"def (_prepare_round_preamble|_run_phases_branch)", r"directive", r"objective"])
show("core/study/guidance.py", [r"^def |^class ", r"directive", r"guidance"])

print("\n──── 环境（预算/事件/追踪/取消）")
show("core/agent/strategy/budget.py", [r"^def ", r"^class ", r"tokens", r"cost", r"time"])
show("core/agent/loop.py", [r"def (_emit|_trace|_fire|_afire_hooks|run|arun)", r"budget", r"cancel", r"trace"])
show("core/agent/event_store.py", [r"^class ", r"def (emit|publish|subscribe|replay)"])
show("core/study/runner.py", [r"evaluate_budget", r"record_usage", r"budget"])

print("\n──── 契约：BranchContext 字段")
p = root / "core/study/phase_engine.py"
t = p.read_text(encoding="utf-8", errors="ignore")
i = t.find("class BranchContext")
print(t[i:i+1400] if i > 0 else "not found")
