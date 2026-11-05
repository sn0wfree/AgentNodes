"""Demo: 完整 strategy-research 工作流示例。

不依赖 LLM（避免需要 API key），展示从 init 到 evaluate 的端到端流程。

运行：
    python examples/demo_workflow.py

清理：
    rm -rf /tmp/demo_agentnodes
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# 让 agentnodes 包可导入
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

WORKSPACE = Path("/tmp/demo_agentnodes")


def run(cmd: list[str], input_text: str | None = None,
         check: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """运行命令并打印。"""
    print(f"\n$ {' '.join(cmd)}")
    return subprocess.run(
        cmd, cwd=cwd, check=check, text=True, capture_output=True,
        input=input_text,
    )


def main() -> int:
    """完整 demo 流程。"""
    # 清理旧 workspace
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)

    # ─────────────────────────────────────────────────────────────
    # Step 0: preflight 检查环境
    # ─────────────────────────────────────────────────────────────
    print("=" * 70)
    print("Step 0: 启动前环境检查 (preflight)")
    print("=" * 70)
    rc = run([
        sys.executable, "-m", "agentnodes.cli", "preflight", str(WORKSPACE),
    ], check=False).returncode
    print(f"  → preflight rc = {rc} (非 0 时为 LLM key 缺失，不影响后续步骤)")

    # ─────────────────────────────────────────────────────────────
    # Step 1: 初始化 workspace
    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("Step 1: 初始化 workspace (init)")
    print("=" * 70)
    proc = run([
        sys.executable, "-m", "agentnodes.cli", "init", str(WORKSPACE),
    ], input_text="test_strat\nrotation\ncalmar\n",
       check=False, cwd=str(REPO_ROOT))
    print(proc.stdout)
    if proc.returncode != 0:
        print(f"❌ init 失败: {proc.stderr}")
        return 1

    # ─────────────────────────────────────────────────────────────
    # Step 2: 查看 workspace 状态
    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("Step 2: 查看 workspace 状态 (status)")
    print("=" * 70)
    proc = run([
        sys.executable, "-m", "agentnodes.cli", "status", str(WORKSPACE),
    ], cwd=str(REPO_ROOT))
    print(proc.stdout)

    # ─────────────────────────────────────────────────────────────
    # Step 3: 修改 strategy.py 添加新因子
    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("Step 3: 修改 strategy.py (添加 vol_20d 因子)")
    print("=" * 70)
    strategy_py = WORKSPACE / "strategies" / "test_strat" / "strategy.py"
    original = strategy_py.read_text(encoding="utf-8")

    augmented = original.replace(
        "    {\n"
        "        \"factor_name\": \"momentum_20_60\",\n"
        "        \"factor_code\": \"ts_mean(close, 20) / ts_mean(close, 60) - 1\",\n"
        "        \"weight\": 1.0,\n"
        "    },\n",
        "    {\n"
        "        \"factor_name\": \"momentum_20_60\",\n"
        "        \"factor_code\": \"ts_mean(close, 20) / ts_mean(close, 60) - 1\",\n"
        "        \"weight\": 0.7,\n"
        "    },\n"
        "    {\n"
        "        \"factor_name\": \"vol_20d\",\n"
        "        \"factor_code\": \"ts_std(ts_return(close, 1), 20)\",\n"
        "        \"weight\": 0.3,\n"
        "    },\n",
    )
    strategy_py.write_text(augmented, encoding="utf-8")
    print(f"  ✓ strategy.py 修改完毕（添加 vol_20d 因子）")

    # ─────────────────────────────────────────────────────────────
    # Step 4: 复跑（evaluate）
    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("Step 4: 复跑当前 strategy.py (evaluate)")
    print("=" * 70)
    proc = run([
        sys.executable, "-m", "agentnodes.cli",
        "evaluate", str(WORKSPACE),
        "--strategy", "test_strat",
        "--description", "momentum + vol 复合",
    ], cwd=str(REPO_ROOT))
    print(proc.stdout)

    # ─────────────────────────────────────────────────────────────
    # Step 5: 列出实验 (list)
    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("Step 5: 列出所有 run (list)")
    print("=" * 70)
    proc = run([
        sys.executable, "-m", "agentnodes.cli", "list", str(WORKSPACE),
        "--strategy", "test_strat",
    ], cwd=str(REPO_ROOT))
    print(proc.stdout)

    # ─────────────────────────────────────────────────────────────
    # Step 6: 复现第一个 run (reproduce)
    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("Step 6: 复现 run_0001 (reproduce)")
    print("=" * 70)
    proc = run([
        sys.executable, "-m", "agentnodes.cli", "reproduce",
        str(WORKSPACE), "run_0001",
        "--strategy", "test_strat",
    ], cwd=str(REPO_ROOT))
    print(proc.stdout)

    # ─────────────────────────────────────────────────────────────
    # Step 7: 查看产物
    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("Step 7: workspace 产物清单")
    print("=" * 70)
    for p in sorted(WORKSPACE.rglob("*")):
        if p.is_file():
            rel = p.relative_to(WORKSPACE)
            size = p.stat().st_size
            print(f"  {size:>8} B  {rel}")

    print(f"\n✅ Demo 完成！清理: rm -rf {WORKSPACE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())