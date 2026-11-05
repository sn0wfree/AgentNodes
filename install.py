#!/usr/bin/env python3
"""Strategy Research 一键安装脚本（跨平台：Windows / Linux / macOS）。

Usage:
    python install.py                  # 完整安装
    python install.py --dev            # 开发模式（不构建前端）
    python install.py --frontend       # 仅构建前端
    python install.py --backend        # 仅安装后端依赖
    python install.py --e2e            # 完整安装 + 跑 E2E 测试 (Playwright)
    python install.py --e2e-only       # 仅跑 E2E 测试（需已安装）
    python install.py --uninstall      # 卸载
    python install.py --help           # 显示帮助

自动检测 ~/.quantnodes/ 配置：
    - 已有 llm.json + .env → 跳过 LLM 设置提示
    - 未配置 → 提示运行 init

启动 Web UI:
    quantnodes-strategy-research serve --host 0.0.0.0 --port 87183
    # 浏览器访问 http://localhost:87183
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# ANSI 颜色（Windows 10+ 支持）
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
NC = "\033[0m"


def log(msg: str) -> None:
    print(f"{GREEN}[INFO]{NC} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}[WARN]{NC} {msg}")


def err(msg: str) -> None:
    print(f"{RED}[ERROR]{NC} {msg}")


def header(msg: str) -> None:
    print(f"\n{BLUE}=== {msg} ==={NC}")


def run(cmd: list[str] | str, cwd: Path | None = None) -> None:
    """运行命令，失败时退出。"""
    cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
    log(f"$ {cmd_str}")
    result = subprocess.run(
        cmd,
        cwd=cwd,
        shell=isinstance(cmd, str),
        env={**__import__("os").environ, "PYTHONUNBUFFERED": "1"},
    )
    if result.returncode != 0:
        err(f"命令失败（退出码 {result.returncode}）")
        sys.exit(result.returncode)


def check_command(name: str) -> None:
    """检查命令是否存在。"""
    if shutil.which(name) is None:
        err(f"{name} 未安装。请先安装 {name}")
        sys.exit(1)


def get_pip() -> str:
    """获取 pip 可执行文件路径。"""
    # 优先使用 python -m pip（更可靠）
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            check=True,
            capture_output=True,
        )
        return f"{sys.executable} -m pip"
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # 退而求其次：直接用 pip / pip3
    for candidate in ("pip3", "pip"):
        if shutil.which(candidate):
            return candidate
    err("未找到 pip。请先安装 pip")
    sys.exit(1)


# ============================================================
# 安装步骤
# ============================================================
def install_backend(project_root: Path, pip: str) -> None:
    """安装 Python 后端依赖。"""
    header("安装后端依赖")

    check_command(sys.executable)

    # 获取版本信息
    py_ver = subprocess.run(
        [sys.executable, "--version"], capture_output=True, text=True
    ).stdout.strip()
    log(f"Python: {py_ver}")
    log(f"pip:    {pip}")

    log("安装包: api + dev + webui-api")
    run([*pip.split(), "install", "-e", ".[api,dev,webui-api]"], cwd=project_root)
    log("后端安装完成 ✓")


def install_frontend_deps(frontend_dir: Path) -> None:
    """安装前端 npm 依赖。"""
    header("安装前端依赖")

    check_command("npm")

    log(f"Node: {subprocess.run(['node', '--version'], capture_output=True, text=True).stdout.strip()}")
    log(f"npm:  {subprocess.run(['npm', '--version'], capture_output=True, text=True).stdout.strip()}")

    run(["npm", "install", "--legacy-peer-deps"], cwd=frontend_dir)
    log("前端依赖安装完成 ✓")


def build_frontend(frontend_dir: Path) -> None:
    """构建前端（输出到 webui/static/）。"""
    header("构建前端")

    log("运行 npm run build ...")
    run(["npm", "run", "build"], cwd=frontend_dir)
    log("前端构建完成 → webui/static/ ✓")


def install_playwright(pip: str) -> None:
    """安装 Playwright Python SDK + Chromium 浏览器。

    - ``pytest-playwright`` 提供 Playwright Python 绑定
    - ``playwright install --with-deps chromium`` 安装 Chromium 浏览器和系统依赖

    系统依赖 (``--with-deps``) 需要 sudo / 管理员权限；如果失败，回退到
    ``playwright install chromium`` （无系统依赖，CI / Docker 中常用）。
    """
    header("安装 Playwright")

    # 检查 pytest-playwright 已安装
    log("检查 playwright Python 包 ...")
    rc = subprocess.run(
        [sys.executable, "-c", "import playwright; print(playwright.__version__)"],
        capture_output=True,
    )
    if rc.returncode == 0:
        log(f"已安装 (version={rc.stdout.decode().strip()}) ✓")
    else:
        log("pip install pytest-playwright (含 playwright SDK)")
        run([*pip.split(), "install", "pytest-playwright"], cwd=Path.cwd())

    # 安装 Chromium 浏览器
    log("安装 Chromium 浏览器二进制 ...")
    rc = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
    )
    if rc.returncode != 0:
        warn("playwright install --with-deps 失败（可能需要 sudo）")
        log("尝试 fallback（不带系统依赖）...")
        rc = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
        )
        if rc.returncode != 0:
            err("Chromium 安装失败。请手动运行：")
            err(f"  {sys.executable} -m playwright install chromium")
            sys.exit(1)
        log("Chromium 安装完成（无系统依赖）✓")
    else:
        log("Chromium 安装完成 ✓")


def check_static_build(project_root: Path) -> None:
    """确保 webui/static/ 已构建（E2E 测试必需）。"""
    static_index = project_root / "webui" / "static" / "index.html"
    if not static_index.exists():
        err(f"前端构建产物不存在: {static_index}")
        err("请先运行: python install.py  或  python install.py --frontend")
        sys.exit(1)


def run_e2e_tests(project_root: Path) -> int:
    """跑 Playwright E2E 测试 + 视觉回归测试。

    使用 ``STRATEGY_RESEARCH_TEST_CHAT=1`` 触发后端脚本化 SSE 模式
    （不需要真实 LLM）。返回 pytest 退出码。
    """
    header("运行 E2E 测试")

    check_static_build(project_root)

    cmd = [
        sys.executable, "-m", "pytest",
        "tests/test_webui_e2e_playwright.py",
        "tests/test_webui_visual.py",
        "-v", "--tb=short",
    ]
    env = {**__import__("os").environ, "STRATEGY_RESEARCH_TEST_CHAT": "1"}

    log("$ " + " ".join(cmd))
    log("(脚本化 SSE 模式 — 不需要真实 LLM)")
    log("视觉回归测试：tests/baselines/ 必须存在 (首次运行 UPDATE_SNAPSHOTS=1 录制)")
    log(" ")
    result = subprocess.run(cmd, cwd=project_root, env=env)
    if result.returncode == 0:
        log("E2E 测试通过 ✓")
    else:
        err("E2E 测试失败")
        err("调试: STRATEGY_RESEARCH_TEST_CHAT=1 python -m pytest tests/test_webui_e2e_playwright.py tests/test_webui_visual.py -v --tb=long")
        err("视觉回归失败时，diff 图保存到 tests/diffs/")
    return result.returncode


def record_visual_baselines(project_root: Path) -> int:
    """录制视觉回归基线（首次或大改后）。"""
    header("录制视觉回归基线")

    check_static_build(project_root)

    cmd = [
        sys.executable, "-m", "pytest",
        "tests/test_webui_visual.py",
        "-v",
    ]
    env = {
        **__import__("os").environ,
        "STRATEGY_RESEARCH_TEST_CHAT": "1",
        "UPDATE_SNAPSHOTS": "1",
    }
    log("$ " + " ".join(cmd))
    log("(UPDATE_SNAPSHOTS=1 → 截图保存为基线，跳过断言)")
    log(" ")
    result = subprocess.run(cmd, cwd=project_root, env=env)
    if result.returncode == 0:
        log("基线录制完成 ✓")
        log(f"位置: tests/baselines/")
    else:
        err("基线录制失败")
    return result.returncode


def uninstall_all(project_root: Path, frontend_dir: Path, pip: str) -> None:
    """卸载：删除包 + node_modules + 构建产物。"""
    header("卸载")

    warn("删除 Python 包 ...")
    subprocess.run(
        [*pip.split(), "uninstall", "-y", "quantnodes-strategy-research"],
        capture_output=True,
    )

    warn("删除前端 node_modules ...")
    if (frontend_dir / "node_modules").exists():
        shutil.rmtree(frontend_dir / "node_modules")

    warn("删除前端构建产物 ...")
    static_dir = project_root / "webui" / "static"
    if static_dir.exists():
        shutil.rmtree(static_dir)

    log("卸载完成 ✓")


def check_llm_config_via_module() -> dict:
    """调用项目内置的检测模块，返回 LLM 配置状态。

    Returns:
        dict with keys: configured, provider, model, api_key_source
    """
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from agentnodes.cli.llm_config_check import check_llm_config
        return check_llm_config()
    except Exception:
        return {
            "configured": False,
            "provider": "",
            "model": "",
            "api_key_source": "none",
        }


def show_usage() -> None:
    """显示使用说明。"""
    header("使用说明")

    # 检测 LLM 配置
    status = check_llm_config_via_module()

    print("✅ 安装完成！\n")

    if status["configured"]:
        # 已配置：跳过 init 提示
        print(f"{GREEN}✓ LLM 配置：已检测到{NC}")
        print(f"   ~/.quantnodes/llm.json  →  provider={status['provider']}, model={status['model']}")
        print(f"   api_key 来源             →  {status['api_key_source']}")
        print(f"   → 跳过 LLM 设置，直接启动即可\n")
    else:
        # 未配置：提示用户
        print(f"{YELLOW}⚠ LLM 配置：未检测到 ~/.quantnodes/llm.json 或缺少 API key{NC}\n")
        print("   启动 Web UI 之前，请先配置 LLM（任选其一）：")
        print("   1. 交互式向导：")
        print("      quantnodes-research init")
        print("   2. 手动创建配置文件：")
        print("      mkdir -p ~/.quantnodes")
        print('      echo \'LLM_API_KEY=sk-...\' > ~/.quantnodes/.env')
        print("      chmod 600 ~/.quantnodes/.env")
        print("   3. 设置环境变量：")
        print('      export OPENAI_API_KEY="sk-..."\n')

    print("""📦 启动 Web UI（推荐）：
   quantnodes-strategy-research serve --host 0.0.0.0 --port 87183
   # 浏览器访问 http://localhost:87183

📦 启动 TUI（终端界面）：
   quantnodes-strategy-research

📦 启动纯 API：
   quantnodes-strategy-research api serve --port 8765

🧪 运行测试：
   python install.py --e2e                         # CI: 完整安装 + E2E
   pytest tests/test_webui_api.py tests/test_webui_e2e.py -v   # 后端 22 测试
   cd webui/frontend && npm test                                 # 前端 27 测试

🛠  开发模式：
   后端: quantnodes-strategy-research serve --reload --port 87183
   前端: cd webui/frontend && npm run dev   # http://localhost:5173
""")


# ============================================================
# CLI
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Strategy Research 一键安装脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dev", action="store_true",
        help="开发模式（不构建前端）",
    )
    parser.add_argument(
        "--frontend", action="store_true",
        help="仅构建前端",
    )
    parser.add_argument(
        "--backend", action="store_true",
        help="仅安装后端依赖",
    )
    parser.add_argument(
        "--e2e", action="store_true",
        help="完整安装 + 跑 E2E 测试（Playwright 浏览器 + 真实后端）",
    )
    parser.add_argument(
        "--e2e-only", action="store_true",
        help="仅跑 E2E 测试（需已通过 (无参数) / --dev 完成安装）",
    )
    parser.add_argument(
        "--record-baselines", action="store_true",
        help="录制视觉回归基线（首次或大改 UI 后）",
    )
    parser.add_argument(
        "--uninstall", action="store_true",
        help="卸载",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).parent.resolve()
    project_root = script_dir
    frontend_dir = project_root / "webui" / "frontend"
    pip = get_pip()

    # 确定模式
    if args.uninstall:
        uninstall_all(project_root, frontend_dir, pip)
        return 0

    if args.backend:
        install_backend(project_root, pip)
        return 0

    if args.frontend:
        install_frontend_deps(frontend_dir)
        build_frontend(frontend_dir)
        return 0

    if args.e2e_only:
        install_playwright(pip)
        rc = run_e2e_tests(project_root)
        return rc

    if args.record_baselines:
        install_playwright(pip)
        rc = record_visual_baselines(project_root)
        return rc

    if args.dev:
        install_backend(project_root, pip)
        install_frontend_deps(frontend_dir)
        show_usage()
        return 0

    if args.e2e:
        install_backend(project_root, pip)
        install_frontend_deps(frontend_dir)
        build_frontend(frontend_dir)
        install_playwright(pip)
        rc = run_e2e_tests(project_root)
        if rc == 0:
            show_usage()
        return rc

    # 默认：完整安装
    install_backend(project_root, pip)
    install_frontend_deps(frontend_dir)
    build_frontend(frontend_dir)
    show_usage()
    return 0


if __name__ == "__main__":
    sys.exit(main())