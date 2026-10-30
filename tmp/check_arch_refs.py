import io
import os
import re

# 检查这 11 个测试文件里 _archived 的引用形态：
#   - 仅注释/字符串提及（可忽略）
#   - 真实 import _archived 模块（需处理）
NAMES = ("activity", "chat_mutex_legacy", "checkpoint", "dag", "dag_engine",
         "event_store", "integration", "knowledge", "runner_context",
         "signals", "streaming", "study_io")

for f in (
    "test_architecture_gates.py", "test_archive_race.py", "test_engine_common.py",
    "test_runner_lifecycle.py", "test_scheduler_state_machine.py", "test_search_like.py",
    "test_study_actions.py", "test_study_consistency_fixes.py", "test_study_objective.py",
    "test_study_scheduler.py", "test_webui_api.py",
):
    path = os.path.join("tests", f)
    real = []
    mentions = 0
    for i, line in enumerate(io.open(path, encoding="utf-8").read().splitlines(), 1):
        if "_archived" not in line:
            continue
        mentions += 1
        if re.search(r"^\s*(from|import)\s", line) and any(
            re.search(rf"\b{n}\b", line) for n in NAMES
        ):
            real.append(f"  {i}: {line.strip()[:100]}")
        elif "import" in line:
            real.append(f"  {i} (import-ish): {line.strip()[:100]}")
    print(f"{f}: mentions={mentions}")
    for r in real:
        print(r)
