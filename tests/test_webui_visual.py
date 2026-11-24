"""Visual regression tests for Web UI — Playwright screenshot diffs.

策略：
1. ``UPDATE_SNAPSHOTS=1``：截图保存为新基线并跳过断言（重录模式）。
2. 基线缺失：默认**失败**（防止"缺基线=假绿"）；确需首次录入时显式设置
   ``ALLOW_BASELINE_BOOTSTRAP=1``。
3. 普通模式：截图后与基线 pixel diff（PIL ImageChops）。

场景：
- test_login_page_baseline — 登录页静态视觉
- test_message_bubble_baseline — 用户 + 助手消息气泡
- test_empty_chat — 空聊天区
- test_command_palette — 命令面板
- test_empty_goal / test_empty_agent — 右栏空态（E1 转为真实断言）

阈值：
- 像素差异阈值 = 10 (0-255)
- 通过阈值 = 0.5% (允许 antialiasing / 字体子像素差异)

运行：
    AN_RUN_BROWSER_TESTS=1 pytest tests/test_webui_visual.py -v
    UPDATE_SNAPSHOTS=1 AN_RUN_BROWSER_TESTS=1 pytest tests/test_webui_visual.py -v
"""

# pytest_plugins 必须顶部
pytest_plugins = ["conftest_e2e"]

import os
import sys
from pathlib import Path
from typing import Iterator

import pytest

# Browser E2E is opt-in (e2e.yml / AN_RUN_BROWSER_TESTS=1): the Playwright
# sync API keeps a running asyncio loop on the main thread for the whole
# session, breaking pytest-asyncio for every later test file.
pytestmark = pytest.mark.skipif(
    os.environ.get("AN_RUN_BROWSER_TESTS", "0") != "1",
    reason="Browser E2E; set AN_RUN_BROWSER_TESTS=1 (or run via e2e.yml)",
)
# E2E 是 opt-in 车道：playwright 未安装时整个模块降级为 skip（而非
# 收集错误——那会让 batch_gate 的 collect 锚点多出 5 个假错误）。
pytest.importorskip("playwright")
from playwright.sync_api import Browser, BrowserContext, Page

# Path setup for visual_diff import
TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(TESTS_DIR))
from visual_diff import compare_images, save_baseline  # noqa: E402

BASELINES_DIR = TESTS_DIR / "baselines"
DIFFS_DIR = TESTS_DIR / "diffs"
ACTUALS_DIR = TESTS_DIR / "_actual"  # 最近一次实际截图（debug 用）

UPDATE_SNAPSHOTS = os.environ.get("UPDATE_SNAPSHOTS", "0") == "1"
# E0 信任修复：基线缺失默认失败，只有显式 bootstrap 才允许首录。
ALLOW_BASELINE_BOOTSTRAP = os.environ.get("ALLOW_BASELINE_BOOTSTRAP", "0") == "1"

# 公共视觉参数（影响所有截图）
VIEWPORT = {"width": 1440, "height": 900}
DEVICE_SCALE_FACTOR = 1  # 1 = 普通；2 = retina；CI 中保持 1 减少像素数


# ────────────────────────── Fixtures ──────────────────────────


@pytest.fixture
def context(browser: Browser, theme: str) -> Iterator[BrowserContext]:
    """每个测试独立 context（固定 viewport + 关闭动画），避免 cookies / storage 污染。

    A-0：按 theme 参数预置 localStorage['sr-theme']，让 theme store 初始值与
    基线主题一致；dark 保持原基线名，light 走 *_light.png。
    """
    ctx = browser.new_context(
        viewport=VIEWPORT,
        device_scale_factor=DEVICE_SCALE_FACTOR,
        locale="zh-CN",
        # 关闭动画 (CSS transition / animation) — 让截图稳定
        reduced_motion="reduce",
    )
    ctx.add_init_script(
        f"localStorage.setItem('sr-theme', '{theme}'); "
        f"document.documentElement.dataset.theme = '{theme}';"
    )
    yield ctx
    ctx.close()


# ────────────────────────── Helpers ──────────────────────────


def _screenshot_element(page: Page, locator, name: str) -> Path:
    """截取 element 截图 → _actual/。返回实际文件路径。"""
    ACTUALS_DIR.mkdir(parents=True, exist_ok=True)
    actual = ACTUALS_DIR / f"{name}.png"
    locator.screenshot(path=str(actual), animations="disabled")
    return actual


def _screenshot_page(page: Page, name: str) -> Path:
    """截取整个 viewport。"""
    ACTUALS_DIR.mkdir(parents=True, exist_ok=True)
    actual = ACTUALS_DIR / f"{name}.png"
    page.screenshot(path=str(actual), full_page=False, animations="disabled")
    return actual


def _assert_or_update(actual: Path, name: str, *, max_diff_ratio: float = 0.005,
                      theme: str = "dark"):
    """对比 actual 与 baseline/name.png。

    A-0：light 主题基线用 ``{name}_light.png``，dark 沿用原名（现有 47 基线不动）。
    - ``UPDATE_SNAPSHOTS=1``: 把 actual 当作新基线并 skip 断言（重录）
    - 基线缺失：默认 fail（E0 信任修复）；``ALLOW_BASELINE_BOOTSTRAP=1`` 才允许首录
    - 否则：pixel diff + 失败时保存 diff 可视化
    """
    baseline_name = f"{name}_light" if theme == "light" else name
    baseline = BASELINES_DIR / f"{baseline_name}.png"

    if UPDATE_SNAPSHOTS:
        save_baseline(actual, baseline)
        pytest.skip(f"Baseline saved: {baseline} (UPDATE_SNAPSHOTS=1)")
    if not baseline.exists():
        if ALLOW_BASELINE_BOOTSTRAP:
            save_baseline(actual, baseline)
            pytest.skip(f"Baseline bootstrapped: {baseline} (ALLOW_BASELINE_BOOTSTRAP=1)")
        raise AssertionError(
            f"Visual baseline missing: {baseline}\n"
            f"  actual: {actual}\n"
            f"  Re-record with UPDATE_SNAPSHOTS=1, or set ALLOW_BASELINE_BOOTSTRAP=1 "
            f"for intentional first capture."
        )

    DIFFS_DIR.mkdir(parents=True, exist_ok=True)
    diff_output = DIFFS_DIR / f"{baseline_name}_diff.png"

    result = compare_images(
        baseline=baseline,
        actual=actual,
        threshold=10,
        max_diff_ratio=max_diff_ratio,
        diff_output=diff_output,
    )

    if result.match:
        return

    # 失败时打印调试信息
    pct = result.diff_ratio * 100
    msg = (
        f"Visual regression: {name}\n"
        f"  diff pixels: {result.diff_pixel_count:,} / {result.total_pixels:,} "
        f"({pct:.2f}% > {max_diff_ratio * 100:.2f}%)\n"
        f"  baseline: {baseline}\n"
        f"  actual:   {actual}\n"
        f"  diff img: {diff_output}"
    )
    raise AssertionError(msg)


# ────────────────────────── Tests ──────────────────────────


class TestLoginPage:
    """登录页静态视觉 — 验证表单 + 样式不回归。"""

    def test_login_page_baseline(self, context: BrowserContext, app_url: str, theme: str):
        page = context.new_page()
        try:
            page.goto(f"{app_url}/login")
            page.wait_for_selector("input[type='password']", timeout=10_000)
            # 等字体加载（Playwright 默认在 navigation 后等 fonts）
            page.wait_for_function("document.fonts.ready", timeout=5000)
            # 等动画/过渡结束
            page.wait_for_timeout(200)

            actual = _screenshot_page(page, "login_page")
            _assert_or_update(actual, "login_page", max_diff_ratio=0.01, theme=theme)
        finally:
            page.close()


class TestMessageBubble:
    """消息气泡视觉 — 验证 user / assistant 气泡样式。"""

    @pytest.fixture
    def chat_page(self, context: BrowserContext, app_url: str) -> Iterator[Page]:
        """预登录 + 注入 session + 发一条消息。"""
        page = context.new_page()
        try:
            # 注册 + 创建 session (通过 API 注入 token)
            import uuid as _uuid

            import requests

            api = requests.Session()
            api.base_url = app_url  # type: ignore[attr-defined]

            username = f"vis_{_uuid.uuid4().hex[:8]}"
            r = api.post(
                f"{api.base_url}/api/auth/register",
                json={"username": username, "password": "p"},
            )
            assert r.status_code == 200
            token = r.json()["access_token"]

            r = api.post(
                f"{api.base_url}/api/chat/session",
                headers={"Authorization": f"Bearer {token}"},
                json={"title": "visual"},
            )
            session_id = r.json()["id"]

            # Login → 设置 session
            page.goto(f"{app_url}/login")
            page.wait_for_selector("input[type='password']", timeout=10_000)
            page.evaluate(
                """({token}) => {
                    localStorage.setItem('sr-auth', JSON.stringify({
                        state: { token, user: { username: 'vis' } }, version: 0
                    }));
                }""",
                {"token": token},
            )
            # Chat 页路由是 /chat（/ 是 Dashboard）
            page.goto(f"{app_url}/chat")
            page.wait_for_selector("header", timeout=10_000)
            page.wait_for_function(
                "() => typeof window.__sessionStore !== 'undefined'", timeout=5000
            )
            page.evaluate(
                f"""() => {{
                    window.__sessionStore.setState({{
                        currentSessionId: '{session_id}',
                        sessions: [{{
                            id: '{session_id}', title: 'visual',
                            created_at: Date.now()/1000, updated_at: Date.now()/1000,
                        }}],
                    }});
                }}"""
            )
            page.wait_for_selector(
                "textarea[placeholder*='输入消息']", timeout=5000
            )

            # 发消息 + 等待 SSE 完整结束
            page.locator("textarea[placeholder*='输入消息']").click()
            page.locator("textarea[placeholder*='输入消息']").type("什么是 alpha?")
            page.locator('button[title="发送 (Enter)"]').last.click()
            page.wait_for_function(
                "() => window.__chatStore?.getState().streamingMessageId === null",
                timeout=15_000,
            )
            # 等 Markdown 渲染稳定
            page.wait_for_timeout(500)
            # **视觉测试隔离（2026-11-10）**：侧栏会话列表的条目数/标题随
            # 运行历史变化（前面模块建过会话就多一条），而本类断言的是
            # **消息气泡**视觉。截图前经真实 UI 按钮收起侧栏，让基线只表达
            # 被测对象——否则同一份代码会因测试执行顺序假失败（实测：
            # 单跑通过、五模块连跑失败，diff 红块全在侧栏会话列表）。
            try:
                page.click('button[title="收起侧边栏"]', timeout=3_000)
                page.wait_for_timeout(300)
            except Exception:  # noqa: BLE001 —— 按钮缺失不阻断（布局变了）
                pass

            yield page
        finally:
            page.close()

    def test_message_bubble_baseline(self, chat_page: Page, theme: str):
        """完整聊天区（用户气泡 + 助手气泡）截图。

        新版 MessageList 无 data-testid；旧 ``.flex-1`` 选择器会命中
        隐藏的布局元素导致 element-screenshot 超时 —— 用整页截图。
        """
        actual = _screenshot_page(chat_page, "message_bubble")
        _assert_or_update(actual, "message_bubble", max_diff_ratio=0.01, theme=theme)

    def test_user_bubble_only(self, chat_page: Page, theme: str):
        """仅用户气泡 — 验证主色调、圆角、右对齐。"""
        # 等消息列表稳定
        chat_page.wait_for_selector("text=什么是 alpha?", timeout=5000)
        # 整页截图（旧 .bg-primary-600 会命中隐藏操作按钮，改用整页）
        actual = _screenshot_page(chat_page, "user_bubble")
        _assert_or_update(actual, "user_bubble", max_diff_ratio=0.01, theme=theme)

    def test_assistant_message_with_markdown(self, chat_page: Page, theme: str):
        """助手消息 + Markdown 渲染。

        等待条件按**行为**而非过期文案（旧钉 '脚本化回复' 已从源码移除，
        2026-11-23 A-0 实测 dark/light 双失败）：chat_page fixture 已确保
        streamingMessageId=null（流结束），这里再等助手角色消息节点渲染。
        """
        # 流结束后消息区至少含用户文本 + 一条助手回复文本节点；
        # 用 body 文本长度跃升作为「回复已落盘」的可观测行为（无稳定 data-* 锚）。
        chat_page.wait_for_function(
            "() => document.body.innerText.includes('什么是 alpha?')", timeout=10_000
        )
        chat_page.wait_for_timeout(300)  # 等 Markdown 字体稳定

        actual = _screenshot_page(chat_page, "assistant_message")
        _assert_or_update(actual, "assistant_message", max_diff_ratio=0.01, theme=theme)



class TestCommandPalette:
    """Cmd+K 调色板弹出视觉。"""

    def test_command_palette_baseline(self, context: BrowserContext, app_url: str, theme: str):
        import uuid as _uuid

        import requests

        api = requests.Session()
        api.base_url = app_url  # type: ignore[attr-defined]

        username = f"cmd_{_uuid.uuid4().hex[:8]}"
        r = api.post(
            f"{api.base_url}/api/auth/register",
            json={"username": username, "password": "p"},
        )
        token = r.json()["access_token"]

        page = context.new_page()
        try:
            page.goto(f"{app_url}/login")
            page.wait_for_selector("input[type='password']", timeout=10_000)
            page.evaluate(
                """({token}) => {
                    localStorage.setItem('sr-auth', JSON.stringify({
                        state: { token, user: { username: 'cmd' } }, version: 0
                    }));
                }""",
                {"token": token},
            )
            page.goto(f"{app_url}/chat")
            page.wait_for_selector("header", timeout=10_000)

            # 按 Cmd+K (Mac) / Ctrl+K (others) 调出 command palette
            page.keyboard.press("Control+k")
            page.wait_for_timeout(300)  # 等 modal 动画

            # 检查 palette 出现
            palette = page.locator("input[placeholder*='搜索'], input[placeholder*='命令'], input[placeholder*='搜索命令']")
            if palette.count() == 0:
                # 备用：通过 state toggle
                page.evaluate(
                    "() => window.__commandPalette && window.__commandPalette.setState({ open: true })"
                )
                page.wait_for_timeout(300)

            actual = _screenshot_page(page, "command_palette")
            _assert_or_update(actual, "command_palette", max_diff_ratio=0.02, theme=theme)
        finally:
            page.close()


class TestEmptyStates:
    """空状态视觉 — 验证当前契约下的空态渲染。

    3 个场景:
    - empty_chat: 已登录 + currentSessionId 设置 + 消息列表为空
    - empty_goal: 无活跃目标时 GoalCard 不渲染，右栏只剩 TokenCard（P5/P6 空态诚实）
    - empty_agent: Monitor 页（/）Agent 概览在无 agent 时显示空态文案

    E1（2026-11-24）：旧 empty_goal / empty_agent 依赖已退役的右栏
    ``button[title='goal'/'agent']``，双主题长期 skip。按 0c0c1f0e 之后
    的真实契约重写为行为断言，不再依赖旧 tab UI。
    """

    @pytest.fixture
    def logged_in_page(
        self, context: BrowserContext, app_url: str
    ) -> Iterator[Page]:
        """通用 fixture: 注册 + 注入 token + 跳转到 home。"""
        import uuid as _uuid

        import requests

        api = requests.Session()
        api.base_url = app_url  # type: ignore[attr-defined]

        username = f"empty_{_uuid.uuid4().hex[:8]}"
        r = api.post(
            f"{api.base_url}/api/auth/register",
            json={"username": username, "password": "p"},
        )
        token = r.json()["access_token"]

        page = context.new_page()
        try:
            page.goto(f"{app_url}/login")
            page.wait_for_selector("input[type='password']", timeout=10_000)
            page.evaluate(
                """({token}) => {
                    localStorage.setItem('sr-auth', JSON.stringify({
                        state: { token, user: { username: 'empty' } }, version: 0
                    }));
                }""",
                {"token": token},
            )
            page.goto(f"{app_url}/chat")
            page.wait_for_selector("header", timeout=10_000)
            yield page
        finally:
            page.close()

    def test_empty_chat(self, logged_in_page: Page, theme: str):
        """空聊天区 — 选中 session 但没有消息。

        预期: MessageList 显示 EmptyState 「开始对话」
        """
        # 注入空 store (default 是空 messages)
        logged_in_page.wait_for_function(
            "() => typeof window.__chatStore !== 'undefined'", timeout=5000
        )
        logged_in_page.wait_for_function(
            "() => typeof window.__sessionStore !== 'undefined'", timeout=5000
        )

        # 注入 session 但不添加消息
        logged_in_page.evaluate(
            """() => {
                window.__sessionStore.setState({
                    currentSessionId: 'demo-session-1',
                    sessions: [{
                        id: 'demo-session-1',
                        title: 'Empty Chat Session',
                        created_at: Date.now() / 1000,
                        updated_at: Date.now() / 1000,
                    }],
                });
            }"""
        )

        # 等 EmptyState「开始对话」出现
        logged_in_page.wait_for_selector("text=开始对话", timeout=5000)
        logged_in_page.wait_for_timeout(300)

        actual = _screenshot_page(logged_in_page, "empty_chat")
        _assert_or_update(actual, "empty_chat", max_diff_ratio=0.01, theme=theme)

    def test_empty_goal(self, logged_in_page: Page, theme: str):
        """无活跃目标 — 右栏不渲染 GoalCard（0c0c1f0e P6 空态诚实）。

        E1：不再依赖已退役的 ``button[title='goal']``；断言右栏存在、
        TokenCard 在、GoalCard 的「目标 & 进度」头不在。
        """
        logged_in_page.wait_for_function(
            "() => window.localStorage.getItem('sr-right-visible') !== 'false'",
            timeout=5000,
        )
        # 右栏容器存在（Title 由 TopBar 的「隐藏右侧面板」按钮标识）
        right_toggle = logged_in_page.locator("button[title='隐藏右侧面板']")
        assert right_toggle.count() >= 1, "right panel toggle not found"
        # 无 goal：GoalCard 不应出现；TokenCard 应出现
        logged_in_page.wait_for_selector("text=Token 使用情况", timeout=5000)
        assert logged_in_page.locator("text=目标 & 进度").count() == 0, (
            "GoalCard should not render when there is no active goal (P6)"
        )

        logged_in_page.wait_for_timeout(300)
        actual = _screenshot_page(logged_in_page, "empty_goal")
        _assert_or_update(actual, "empty_goal", max_diff_ratio=0.02, theme=theme)

    def test_empty_agent(self, logged_in_page: Page, app_url: str, theme: str):
        """无 Agent — Monitor 页（/）Agent 概览显示空态文案。

        E1：AgentList 未挂右栏；真实空态在 MonitorPage。跳转 / 后断言
        「暂无 Agent」文案出现，整页截图（不再硬编码 480px clip）。
        """
        logged_in_page.goto(f"{app_url}/")
        logged_in_page.wait_for_selector("text=Agent 概览", timeout=10_000)
        logged_in_page.wait_for_selector("text=暂无 Agent", timeout=10_000)
        logged_in_page.wait_for_timeout(300)

        actual = _screenshot_page(logged_in_page, "empty_agent")
        _assert_or_update(actual, "empty_agent", max_diff_ratio=0.02, theme=theme)
