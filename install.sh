#!/usr/bin/env bash
# Strategy Research 一键安装脚本
#
# Usage:
#   ./install.sh                      # 完整安装（构建前端 + 后端）
#   ./install.sh --dev                # 开发模式（不构建前端）
#   ./install.sh --frontend           # 仅构建前端
#   ./install.sh --backend            # 仅安装后端依赖
#   ./install.sh --e2e                # 完整安装 + 跑 E2E + 视觉回归测试 (Playwright)
#   ./install.sh --e2e-only           # 仅跑 E2E + 视觉回归测试（需已安装）
#   ./install.sh --record-baselines   # 录制视觉回归基线（首次/大改 UI 后）
#   ./install.sh --uninstall          # 卸载
#   ./install.sh --help               # 显示帮助
#
# 自动检测 ~/.quantnodes/ 配置：
#   - 已有 llm.json + .env → 跳过 LLM 设置提示
#   - 未配置 → 提示运行 init
#
# 启动 Web UI:
#   agentnodes serve --host 0.0.0.0 --port 87183
#
# 访问 http://localhost:87183

set -e

# 默认参数
MODE="full"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()    { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()   { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()    { echo -e "${RED}[ERROR]${NC} $*"; }
header() { echo -e "\n${BLUE}=== $* ===${NC}"; }

# 解析脚本所在目录（兼容直接运行和软链）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PIP_BIN="${PIP_BIN:-pip3}"

# ============================================================
# LLM 配置检测
# ============================================================
QUANTNODES_DIR="$HOME/.quantnodes"
LLM_JSON_PATH="$QUANTNODES_DIR/llm.json"
DOTENV_PATH="$QUANTNODES_DIR/.env"

check_llm_config() {
    """检测 ~/.quantnodes/ LLM 配置状态。

    输出（全局变量）：
      llm_configured    - "true" / "false"
      llm_provider      - provider 名称（如 minimax/openai）
      llm_model         - model 名称
      llm_api_key_source - env / dotenv / llm.json / none
    """
    # 调用 Python 模块做检测（单一可信源）
    local _result
    _result=$($PYTHON_BIN -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT/src')
from agentnodes.interfaces.cli.llm_config_check import check_llm_config
import json
status = check_llm_config()
print(json.dumps(status, ensure_ascii=False))
" 2>/dev/null || echo '{"configured": false, "provider": "", "model": "", "api_key_source": "none"}')

    # 解析 JSON 结果
    llm_configured=$(echo "$_result" | $PYTHON_BIN -c "import json,sys; print('true' if json.load(sys.stdin).get('configured') else 'false')" 2>/dev/null || echo "false")
    llm_provider=$(echo "$_result" | $PYTHON_BIN -c "import json,sys; print(json.load(sys.stdin).get('provider', ''))" 2>/dev/null || echo "")
    llm_model=$(echo "$_result" | $PYTHON_BIN -c "import json,sys; print(json.load(sys.stdin).get('model', ''))" 2>/dev/null || echo "")
    llm_api_key_source=$(echo "$_result" | $PYTHON_BIN -c "import json,sys; print(json.load(sys.stdin).get('api_key_source', 'none'))" 2>/dev/null || echo "none")
}

# ============================================================
# 帮助
# ============================================================
show_help() {
    cat <<EOF
Strategy Research 一键安装脚本

Usage:
  $0 [options]

Options:
  (无参数)       完整安装：后端 + 前端依赖 + 构建前端
  --dev          开发模式：后端 + 前端依赖（不构建）
  --frontend     仅构建前端
  --backend      仅安装后端依赖
  --e2e          完整安装 + 跑 E2E + 视觉回归测试
  --e2e-only     仅跑 E2E + 视觉回归测试（需已安装）
  --record-baselines  录制视觉回归基线（tests/baselines/）
  --uninstall    卸载（删除包 + node_modules + 构建产物）
  --help         显示此帮助

Examples:
  $0                              # 完整安装
  $0 --dev                        # 开发模式
  $0 --e2e                        # CI 用：完整安装 + E2E + 视觉回归
  $0 --record-baselines           # 首次/大改后：重新录制视觉基线
  agentnodes serve --host 0.0.0.0 --port 87183
EOF
}

# ============================================================
# 检查命令
# ============================================================
check_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        err "$1 未安装。请先安装 $1"
        exit 1
    fi
}

# ============================================================
# 安装后端
# ============================================================
install_backend() {
    header "安装后端依赖"
    check_command "$PYTHON_BIN"
    check_command "$PIP_BIN"

    log "Python: $($PYTHON_BIN --version)"
    log "pip:    $($PIP_BIN --version)"

    cd "$PROJECT_ROOT"
    log "安装包: api + dev + webui-api"
    $PIP_BIN install -e ".[api,dev,webui-api]"
    log "后端安装完成 ✓"
}

# ============================================================
# 安装前端依赖
# ============================================================
install_frontend_deps() {
    header "安装前端依赖"
    check_command npm

    cd "$PROJECT_ROOT/webui/frontend"
    log "npm: $(npm --version)"
    log "Node: $(node --version)"
    npm install --legacy-peer-deps
    log "前端依赖安装完成 ✓"
}

# ============================================================
# 构建前端
# ============================================================
build_frontend() {
    header "构建前端"
    cd "$PROJECT_ROOT/webui/frontend"
    log "运行 npm run build ..."
    npm run build
    log "前端构建完成 → webui/static/ ✓"
}

# ============================================================
# 安装 Playwright Python 包 + 浏览器
# ============================================================
install_playwright() {
    header "安装 Playwright"

    log "检查 playwright Python 包 ..."
    if ! $PYTHON_BIN -c "import playwright" 2>/dev/null; then
        log "pip install pytest-playwright (含 playwright SDK)"
        $PIP_BIN install pytest-playwright
    else
        log "已安装 ✓"
    fi

    log "安装 Chromium 浏览器二进制 (--with-deps) ..."
    log "(如果 --with-deps 需要 sudo，会自动提示)"
    if $PYTHON_BIN -m playwright install --with-deps chromium; then
        log "Chromium 安装完成 ✓"
    else
        warn "playwright install --with-deps 失败"
        log "尝试 fallback（不带系统依赖）..."
        if $PYTHON_BIN -m playwright install chromium; then
            log "Chromium 安装完成（无系统依赖）✓"
        else
            err "Chromium 安装失败。请手动运行: $PYTHON_BIN -m playwright install chromium"
            return 1
        fi
    fi
}

# ============================================================
# 验证前端构建产物存在
# ============================================================
check_static_build() {
    if [[ ! -f "$PROJECT_ROOT/webui/static/index.html" ]]; then
        err "前端构建产物不存在: webui/static/index.html"
        err "请先运行: $0  或  $0 --frontend"
        return 1
    fi
}

# =========================================================─────────
# 跑 E2E + 视觉回归测试
# ============================================================
run_e2e_tests() {
    header "运行 E2E + 视觉回归测试"

    check_command "$PYTHON_BIN"

    # 确保前端已构建（E2E 需要 webui/static/）
    check_static_build || return 1

    cd "$PROJECT_ROOT"

    log "pytest tests/test_webui_e2e_playwright.py tests/test_webui_visual.py -v"
    log "(脚本化 SSE 模式 — 不需要真实 LLM)"
    log "视觉回归需要 tests/baselines/ 存在 (首次: ./install.sh --record-baselines)"
    log " "

    # 禁用 LLMBridge autouse fixture 干扰（conftest.py 有 _isolate_llm_bridge）
    # E2E 用 TEST_MODE=1 替代
    if STRATEGY_RESEARCH_TEST_CHAT=1 "$PYTHON_BIN" -m pytest \
        tests/test_webui_e2e_playwright.py \
        tests/test_webui_visual.py \
        -v --tb=short; then
        log "E2E + 视觉回归测试通过 ✓"
        return 0
    else
        err "测试失败"
        err "调试: STRATEGY_RESEARCH_TEST_CHAT=1 $PYTHON_BIN -m pytest tests/test_webui_e2e_playwright.py tests/test_webui_visual.py -v --tb=long"
        err "视觉回归失败时，diff 图保存到 tests/diffs/"
        return 1
    fi
}

# =========================================================─────────
# 录制视觉回归基线
# =========================================================─────────
record_visual_baselines() {
    header "录制视觉回归基线"

    check_command "$PYTHON_BIN"
    check_static_build || return 1

    cd "$PROJECT_ROOT"

    log "UPDATE_SNAPSHOTS=1 pytest tests/test_webui_visual.py -v"
    log "(截图保存为基线，跳过断言)"
    log " "

    if UPDATE_SNAPSHOTS=1 STRATEGY_RESEARCH_TEST_CHAT=1 "$PYTHON_BIN" -m pytest \
        tests/test_webui_visual.py -v --tb=short; then
        log "基线录制完成 ✓ → tests/baselines/"
        return 0
    else
        err "基线录制失败"
        return 1
    fi
}

# ============================================================
# 卸载
# ============================================================
uninstall_all() {
    header "卸载"
    warn "删除 Python 包 ..."
    $PIP_BIN uninstall -y agentnodes 2>/dev/null || true

    warn "删除前端 node_modules ..."
    rm -rf "$PROJECT_ROOT/webui/frontend/node_modules"

    warn "删除前端构建产物 ..."
    rm -rf "$PROJECT_ROOT/webui/static"

    log "卸载完成 ✓"
}

# ============================================================
# 显示使用说明
# ============================================================
show_usage() {
    header "使用说明"

    # 检测 LLM 配置
    check_llm_config

    cat <<EOF
✅ 安装完成！

EOF

    if [[ "$llm_configured" == "true" ]]; then
        cat <<EOF
✓ LLM 配置：已检测到
   ~/.quantnodes/llm.json  →  provider=$llm_provider, model=$llm_model
   api_key 来源             →  $llm_api_key_source
   → 跳过 LLM 设置，直接启动即可

EOF
    else
        cat <<EOF
⚠ LLM 配置：未检测到 ~/.quantnodes/llm.json 或缺少 API key

   启动 Web UI 之前，请先配置 LLM（任选其一）：
   1. 交互式向导：
      agentnodes init
   2. 手动创建配置文件：
      mkdir -p ~/.quantnodes
      echo 'LLM_API_KEY=sk-...' > ~/.quantnodes/.env
      chmod 600 ~/.quantnodes/.env
   3. 设置环境变量：
      export OPENAI_API_KEY="sk-..."

EOF
    fi

    cat <<'EOF'
📦 启动 Web UI（推荐）：
   agentnodes serve --host 0.0.0.0 --port 87183
   # 浏览器访问 http://localhost:87183

📦 启动 TUI（终端界面）：
   agentnodes

📦 启动纯 API：
   agentnodes api serve --port 8765

🧪 运行测试：
   ./install.sh --e2e                              # CI: 完整安装 + E2E (Playwright)
   pytest tests/test_webui_api.py tests/test_webui_e2e.py -v   # 后端 22 测试
   cd webui/frontend && npm test                                 # 前端 27 测试

🛠  开发模式：
   后端: agentnodes serve --reload --port 87183
   前端: cd webui/frontend && npm run dev   # http://localhost:5173
EOF
}

# ============================================================
# 解析参数
# ============================================================
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            show_help
            exit 0
            ;;
        --dev)
            MODE="dev"
            shift
            ;;
        --frontend)
            MODE="frontend"
            shift
            ;;
        --backend)
            MODE="backend"
            shift
            ;;
        --e2e)
            MODE="e2e"
            shift
            ;;
        --e2e-only)
            MODE="e2e-only"
            shift
            ;;
        --record-baselines)
            MODE="record-baselines"
            shift
            ;;
        --uninstall)
            MODE="uninstall"
            shift
            ;;
        *)
            err "未知参数: $1"
            show_help
            exit 1
            ;;
    esac
done

# ============================================================
# 主入口
# ============================================================
case "$MODE" in
    uninstall)
        uninstall_all
        ;;
    backend)
        install_backend
        ;;
    frontend)
        install_frontend_deps
        build_frontend
        ;;
    dev)
        install_backend
        install_frontend_deps
        show_usage
        ;;
    e2e-only)
        install_playwright
        run_e2e_tests
        ;;
    record-baselines)
        install_playwright
        record_visual_baselines
        ;;
    e2e)
        install_backend
        install_frontend_deps
        build_frontend
        install_playwright
        run_e2e_tests
        if [[ $? -eq 0 ]]; then
            show_usage
        fi
        ;;
    full|"")
        install_backend
        install_frontend_deps
        build_frontend
        show_usage
        ;;
esac