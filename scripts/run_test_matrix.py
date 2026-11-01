"""分 lane + 分片的本地验证矩阵（对齐 CI，替代「混合全量」）。

为什么需要它（docs/test-marker-lanes-design.md 的配套工具）：
`tests/conftest.py` 用文件名白名单把测试分到 llm / e2e / slow 三个 lane。
把 e2e 与单元测试放进**同一个进程**会互相污染（live uvicorn 的全局 app
单例、真实 goal DB、端口与 socket 等待）——实测把 7 分钟的 PR 门拉长到
11-15 分钟，并制造无关失败（2026-11-01 为此浪费约一小时追 5 个假回归）。

本脚本复刻 `.github/workflows/tests.yml` 的做法：

  * PR 门按**文件**分片（scripts/pytest_shard.py --mode time-aware），
    每个分片一个进程——比 pytest-xdist 安全，因为同进程并发会让依赖
    隐式导入顺序的测试互相破坏（实测：`strategy_research.webui.routes`
    已被 W3 删除，仍有 6 个测试 patch 它，串行靠别人导入的副作用才通过）。
  * 每个分片带 `--reruns=2`（与 CI 一致），吸收已知的偶发用例。
  * e2e / browser 单独进程，各自独立超时。

用法：
    python scripts/run_test_matrix.py              # 全部 lane
    python scripts/run_test_matrix.py --lane pr    # 只跑 PR 门
    python scripts/run_test_matrix.py --lane pr --cov
    python scripts/run_test_matrix.py --shards 1   # 不分片（最快启动）

退出码：0 全部 lane 通过；1 有 lane 失败；2 用法错误。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

SHARD_SCRIPT = Path(__file__).with_name("pytest_shard.py")


def _lane_excluded_files() -> list[str]:
    """Files the PR lane's ``-m`` filter will deselect.

    Read from tests/conftest.py so the two never drift. Without this the
    cost model gives e.g. test_alpha_zoo.py (418s, tagged slow) a whole
    shard to itself, ``-m 'not slow'`` then deselects everything, and that
    worker idles while the others do all the work.
    """
    names: set[str] = set()
    try:
        import re

        text = (Path(__file__).resolve().parents[1]
                / "tests" / "conftest.py").read_text(encoding="utf-8")
        for group in ("_LANE_LLM_FILES", "_LANE_E2E_FILES", "_LANE_SLOW_FILES"):
            m = re.search(rf"{group}\s*=\s*frozenset\(\{{(.*?)\}}\)", text, re.S)
            if not m:
                continue
            names.update(re.findall(r'"([^"]+\.py)"', m.group(1)))
    except OSError:
        return []
    return sorted(names)


def _shard_files(shard: int, shards: int, exclude: list[str]) -> list[str]:
    cmd = [sys.executable, str(SHARD_SCRIPT), "--mode", "time-aware",
           "--shard", str(shard), "--shards", str(shards)]
    for pat in exclude:
        cmd += ["--exclude", pat]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]


def _run(files: list[str], extra: list[str],
         extra_env: dict[str, str] | None = None,
         *, isolate: str | None = None) -> tuple[int, float, str, str]:
    """Run pytest on ``files``.

    File lists go through an ``@argfile`` rather than the command line:
    a single shard of this suite is ~646 paths, which exceeds the Windows
    CreateProcess limit (32767 chars) with ``FileNotFoundError: [WinError 206]
    filename or extension too long``.

    ``isolate``: per-shard scratch tag. When set, the shard gets its own
    ``--basetemp`` and ``-p no:cacheprovider`` so parallel shards never share
    pytest's tmp root or ``.pytest_cache`` — sharing them caused flaky
    PermissionError / ImportError storms on Windows (four shards race on the
    same directories).

    The scratch root lives under the SYSTEM temp dir, not the repo: pytest
    would otherwise create tmp_path inside the git worktree, and tests that
    assert "not a git repo" (e.g. GitDiffTool) then walk up and find the
    repo — a failure mode my first attempt at this fix introduced.
    """
    env = dict(os.environ)
    env.update(extra_env or {})
    args = list(extra)
    if isolate:
        import tempfile

        scratch = Path(tempfile.gettempdir()) / "qn-pytest-shards" / isolate
        scratch.mkdir(parents=True, exist_ok=True)
        args += [f"--basetemp={scratch / 'tmp'}", "-p", "no:cacheprovider"]
    tmp_dir = Path.cwd() / ".pytest_args"
    tmp_dir.mkdir(exist_ok=True)
    argfile = tmp_dir / f"shard-{os.getpid()}-{time.perf_counter_ns()}.txt"
    argfile.write_text("\n".join(files), encoding="utf-8")
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", f"@{argfile}", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env,
        )
    finally:
        argfile.unlink(missing_ok=True)
    elapsed = time.perf_counter() - t0
    out = proc.stdout or ""
    tail = out.strip().splitlines()[-1:] or ["(no output)"]
    return proc.returncode, elapsed, tail[0], out


def run_pr(shards: int, cov: bool, jobs: int,
           log_path: str | None = None) -> tuple[int, float, list[str]]:
    """PR 门：按文件分片，每片独立进程；分片之间并行执行。

    并行度用 ``jobs`` 控制（默认 min(shards, cpu-1)）。分片并行安全，
    因为每个分片是一个独立**进程**——这正是它比 pytest-xdist 可靠的原因。
    """
    extra = ["--cov=strategy_research", "--cov-report=term"] if cov else []
    exclude = _lane_excluded_files()
    plans: list[tuple[int, list[str]]] = []
    for i in range(shards):
        files = _shard_files(i, shards, exclude)
        if not files:
            print(f"  shard {i}: empty, skip")
            continue
        plans.append((i, files))

    if not plans:
        return 0, 0.0, []

    t_start = time.perf_counter()
    results: dict[int, tuple[int, float, str, str]] = {}

    def work(shard_idx: int, files: list[str]) -> None:
        # 覆盖率是进程级的：多分片会互相覆盖 .coverage，只在单分片时启用
        shard_extra = extra if shards == 1 else []
        extra_args = ["-q", "--tb=short",
                      "--reruns=2", "--reruns-delay=1",
                      "-m", "not llm and not e2e and not slow", *shard_extra]
        results[shard_idx] = _run(files, extra_args, isolate=f"shard{shard_idx}")

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = [
            pool.submit(work, shard_idx, files)
            for shard_idx, files in plans
        ]
    # .result() propagates any exception raised inside a worker; without it
    # a crashed shard silently leaves results[shard_idx] unset (KeyError).
    for fut in futures:
        fut.result()

    total = time.perf_counter() - t_start
    failed: list[str] = []
    for shard_idx, files in plans:
        rc, elapsed, tail, _out = results[shard_idx]
        print(f"  shard {shard_idx}: {elapsed:6.1f}s  rc={rc}  {tail}")
        if rc != 0:
            failed.append(f"shard {shard_idx}")

    # 合并各分片的完整输出，供 scripts/c1_gate.py / cov_gate.py 判定。
    # （分片让失败信息散落多处，没有合并就无法做「失败集不扩大」检查。）
    if log_path is not None:
        with open(log_path, "w", encoding="utf-8", newline="") as f:
            for shard_idx, _files in plans:
                f.write(f"\n===== shard {shard_idx} =====\n")
                f.write(results[shard_idx][3])
    return (1 if failed else 0), total, failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lane", action="append",
                    choices=["pr", "e2e", "browser"],
                    help="只跑指定 lane（可重复）")
    ap.add_argument("--shards", type=int, default=4,
                    help="PR lane 分片数（默认 4，与 CI 一致；1=不分片）")
    ap.add_argument("--jobs", type=int,
                    help="分片并行度（默认 min(shards, cpu-1)）")
    ap.add_argument("--cov", action="store_true",
                    help="PR lane 附加覆盖率（需 --shards 1）")
    ap.add_argument("--no-reruns", action="store_true",
                    help="关闭 --reruns（用于确认失败是真失败）")
    ap.add_argument("--log", default="tmp/test-matrix.log",
                    help="分片输出合并后的落盘路径（供 c1_gate 判定）")
    args = ap.parse_args()

    lanes = args.lane or ["pr", "e2e", "browser"]
    if args.cov and args.shards != 1:
        print("2: --cov 需要 --shards 1（覆盖率文件是进程级的）", file=sys.stderr)
        return 2

    results: list[tuple[str, int, float, str]] = []

    if "pr" in lanes:
        print("\n=== lane pr (unit, sharded) ===", flush=True)
        jobs = args.jobs or min(args.shards, max(1, (os.cpu_count() or 2) - 1))
        rc, elapsed, bad = run_pr(args.shards, args.cov, jobs, args.log)
        detail = f"all shards green (jobs={jobs})" if rc == 0 else ", ".join(bad)
        results.append(("pr", rc, elapsed, detail))

    if "e2e" in lanes:
        print("\n=== lane e2e ===", flush=True)
        rc, elapsed, tail, _out = _run(
            ["tests"],
            ["-q", "--tb=short", "-m", "e2e"]
            + ([] if args.no_reruns else ["--reruns=2"]))
        print(f"    {elapsed:6.1f}s  rc={rc}  {tail}")
        results.append(("e2e", rc, elapsed, tail))

    if "browser" in lanes:
        print("\n=== lane browser ===", flush=True)
        rc, elapsed, tail, _out = _run(
            ["tests/test_webui_e2e_playwright.py"],
            ["-q", "--tb=short"],
            {"SR_RUN_BROWSER_TESTS": "1"},
        )
        print(f"    {elapsed:6.1f}s  rc={rc}  {tail}")
        results.append(("browser", rc, elapsed, tail))

    print("\n" + "=" * 70)
    print(f"{'lane':<10} {'time':>9}  result")
    for name, rc, elapsed, detail in results:
        print(f"{name:<10} {elapsed:8.1f}s  {'PASS' if rc == 0 else 'FAIL'}  {detail}")
    print(f"{'TOTAL':<10} {sum(r[2] for r in results):8.1f}s")
    print("\n注：PR lane 的失败多半是冻结基线 (tests/baselines/pr-lane-failures.json)"
          " 里的历史账；用 scripts/c1_gate.py 判定是否有新增。")
    return 0 if all(rc == 0 for _, rc, _, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
