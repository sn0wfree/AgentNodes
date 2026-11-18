# -*- coding: utf-8 -*-
"""audit_legacy_chat_api — 旧聊天队列 API 的兼容出口审计（只读，裁决 3 前置）。

为什么存在（2026-11-18 裁决 3）：
「仓库内无调用」≠「外部兼容 API 无消费者」。``send_message`` 已被定为
**兼容 API**（§12.3 裁决 1），直接移除属于对该裁决的变更。本脚本提供
**不依赖旧实现**的只读证据链（纯静态引用扫描 + 重导出链 + env 执行引用），
供兼容期决策与 R-015「独立依赖审计」举证。

**本脚本不调用任何被审计的旧实现**——它只读源码文本与 git 索引。

用法：
    python scripts/arch/audit_legacy_chat_api.py            # 完整报告
    python scripts/arch/audit_legacy_chat_api.py --json     # 机器可读

退出码：0 = 审计完成（**不代表通过裁决**——删除需 R-015 四项 + R-016 +
R-014 方案先行 + R-020 独立批次）；1 = 扫描失败。

命名规范见 scripts/README.md。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 旧队列/执行面 API：定义在 kernel/session/service.py（+ 两层重导出）。
LEGACY_METHODS = [
    "send_message",
    "cancel",
    "cancel_session",
    "resume_queue",
    "wait_for_attempt",
    "is_session_processing",
    "mark_session_processing",
    "list_active_attempts",
    "get_available_actions",
]

#: 重导出链：谁把旧符号再导出一次（外部消费者可能从这里 import）。
REEXPORT_CHAINS = [
    ("kernel/session/__init__.py", "_CHAT_ATTEMPT_TIMEOUT"),
    ("interfaces/api/session/service.py", "_CHAT_ATTEMPT_TIMEOUT"),
]

#: 与旧队列绑定的 env（删除旧实现后其语义是否仍在别处生效）。
BOUND_ENV = [
    "AGENTNODES_CHAT_ATTEMPT_TIMEOUT",
    "AGENTNODES_EVENT_LOG_READ",
]

#: 通用方法名——同名调用可能属于**别的**接收者（task.cancel() / cancel.set()），
#: 不能直接计入"旧 API 生产调用"。这类出口只列候选，须人工判定。
AMBIGUOUS_METHODS = {"cancel", "cancel_session", "list_active_attempts"}

#: 明显不是 SessionService 的接收者前缀（用于歧义出口的候选过滤）。
NOT_SESSION_SERVICE = re.compile(
    r"\b(task|_task|loop|_loop|timer|_timer|_verb_timer|bridge|runtime|worker|"
    r"scope|event|proc|process|thread|future|loop_|_cancel|cancel_)\w*\s*\.\s*"
)

#: 定义文件自身（service.py）——其内部调用是实现细节，不是"外部消费者"。
SERVICE_FILE = "src/agentnodes/kernel/session/service.py"

#: 判定"接收者是 SessionService 实例"的标识符形态
#: （svc / service / session_service / _service 等）。
SESSION_SERVICE_RECEIVER = re.compile(
    r"\b(svc|service|session_service|_service|session_svc|ms|mgr)\b"
)

#: 显式非 SessionService 的接收者（Gateway 有同名方法，必须排除）。
NON_SESSION_RECEIVER = re.compile(r"\b(gateway|gw|swarm|runtime|worker|bridge|scope|task)\b")


def git_grep(pattern: str, paths: list[str]) -> list[str]:
    out = subprocess.run(
        ["git", "grep", "-n", "-E", pattern, "--"] + paths,
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace").stdout
    return [ln for ln in out.splitlines() if ln.strip()]


def classify(line: str) -> tuple[str, bool]:
    """返回 (分组, 是否为调用点)。分组：src/tests/scripts/docs。"""
    path = line.split(":", 1)[0]
    if path.startswith("src/"):
        group = "src"
    elif path.startswith("tests/"):
        group = "tests"
    elif path.startswith("scripts/"):
        group = "scripts"
    elif path.startswith("docs/"):
        group = "docs"
    else:
        group = "other"
    code = line.split(":", 2)[-1]
    is_call = bool(re.search(r"\.\w+\s*\(", code))
    return group, is_call


def scan_method(method: str) -> dict:
    hits = git_grep(rf"\b{method}\b", ["src", "tests", "scripts", "docs"])
    groups: dict[str, list[str]] = {"src": [], "tests": [], "scripts": [], "docs": [], "other": []}
    calls: dict[str, list[str]] = {k: [] for k in groups}
    ambiguous: list[str] = []
    for ln in hits:
        g, is_call = classify(ln)
        groups[g].append(ln)
        if not is_call:
            continue
        code = ln.split(":", 2)[-1]
        # 日志/字符串误判（logger.info("... send_message ...")) 不是调用
        if "logger." in code or code.strip().startswith(('"', "'")):
            continue
        # 定义文件自身的调用 = 实现细节，不是外部消费者
        if ln.startswith(SERVICE_FILE + ":"):
            continue
        if method in AMBIGUOUS_METHODS:
            # 同名歧义：按**接收者归属**判定（gateway.cancel_session ≠
            # service.cancel_session；runtime.cancel ≠ svc.cancel）
            recv = re.search(
                r"([\w\.\[\]'\"]+)\s*\.\s*" + re.escape(method) + r"\s*\(", code,
            )
            receiver = recv.group(1) if recv else ""
            if NON_SESSION_RECEIVER.search(receiver) or \
                    not SESSION_SERVICE_RECEIVER.search(receiver):
                ambiguous.append(ln)
                continue
        calls[g].append(ln)
    # 定义点（def 名字）
    defs = [ln for ln in hits if re.search(rf"def\s+{method}\b", ln)]
    return {
        "method": method,
        "definitions": defs,
        "src_calls": calls["src"],
        "tests_calls": calls["tests"],
        "scripts_calls": calls["scripts"],
        "docs_mentions": len(groups["docs"]),
        "ambiguous_same_name": ambiguous,
    }


def scan_reexports() -> list[dict]:
    out = []
    for rel, symbol in REEXPORT_CHAINS:
        p = ROOT / "src" / "agentnodes" / rel
        exists = p.exists()
        hit = False
        if exists:
            hit = symbol in p.read_text(encoding="utf-8", errors="replace")
        out.append({
            "path": f"src/agentnodes/{rel}",
            "symbol": symbol,
            "exists": exists,
            "reexports": hit,
        })
    return out


def scan_env(env: str) -> dict:
    hits = git_grep(re.escape(env), ["src", "tests", "scripts"])
    src_hits = [h for h in hits if h.startswith("src/")]
    # 执行引用 = 在 src 里读取该 env（非仅声明/文档）
    reads = [
        h for h in src_hits
        if re.search(rf"get_(?:str|int|float|bool)\(\s*[\"']{re.escape(env)}[\"']", h)
        or re.search(rf"\b{re.escape(env)}\b", h) and "get_" in h
    ]
    return {
        "env": env,
        "src_total": len(src_hits),
        "src_reads": reads,
        "files": sorted({h.split(":", 1)[0] for h in src_hits}),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    methods = [scan_method(m) for m in LEGACY_METHODS]
    reexports = scan_reexports()
    envs = [scan_env(e) for e in BOUND_ENV]

    prod_callers = {m["method"]: len(m["src_calls"]) for m in methods}
    total_prod = sum(prod_callers.values())

    report = {
        "methods": methods,
        "reexports": reexports,
        "envs": envs,
        "production_callers_total": total_prod,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("=" * 74)
    print("旧聊天队列 API 兼容出口审计（只读；删除需 R-015/R-016/R-014/R-020 独立批次）")
    print("=" * 74)

    print("\n== 1. 逐出口：定义 / 生产调用 / 测试调用 / 文档提及 ==")
    for m in methods:
        print(f"\n  {m['method']}")
        for d in m["definitions"]:
            print(f"      定义  {d[:104]}")
        prod = m["src_calls"]
        print(f"      生产调用 {len(prod)}"
              + ("" if not prod else "  ← 需逐条判定"))
        for c in prod:
            print(f"         {c[:104]}")
        print(f"      测试调用 {len(m['tests_calls'])} · 脚本 {len(m['scripts_calls'])}"
              f" · 文档提及 {m['docs_mentions']}")
        if m["ambiguous_same_name"]:
            print(f"      同名歧义候选（非 SessionService 接收者，人工判定）：{len(m['ambiguous_same_name'])}")
            for c in m["ambiguous_same_name"][:4]:
                print(f"         ? {c[:100]}")
            if len(m["ambiguous_same_name"]) > 4:
                print(f"         … 共 {len(m['ambiguous_same_name'])}")

    print("\n== 2. 两层重导出（外部消费者可能从这里 import）==")
    for r in reexports:
        state = "存在且重导出" if r["reexports"] else (
            "存在但未重导出" if r["exists"] else "文件不存在")
        print(f"  {r['path']}  [{r['symbol']}]  {state}")

    print("\n== 3. 与旧队列绑定的 env（删除后语义是否仍有承载点）==")
    for e in envs:
        print(f"\n  {e['env']}  src 命中 {e['src_total']}")
        for f in e["files"]:
            print(f"      {f}")
        if e["src_reads"]:
            print("      执行读取点：")
            for r in e["src_reads"]:
                print(f"        {r[:104]}")

    print("\n== 4. 结论摘要 ==")
    print(f"  旧队列 API 的 src 生产调用点合计：{total_prod}")
    for k, v in prod_callers.items():
        print(f"      {k:<26} {v}")
    if total_prod == 0:
        print("  → 仓库内无生产调用（**不等于**外部无消费者——兼容 API 需兼容期公告）")
    print("  → 静态扫描无法发现：动态 import、插件、仓库外消费者、反射调用")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
