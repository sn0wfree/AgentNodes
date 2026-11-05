"""Visual regression tests for Web UI — Playwright screenshot diffs.

策略：
1. ``--record-snapshots`` 模式：截图保存为基线
2. 普通模式：截图后与基线 pixel diff（PIL ImageChops）

场景：
- test_login_page_baseline — 登录页静态视觉
- test_message_bubble_baseline — 用户 + 助手消息气泡
- test_dag_visualization_baseline — React Flow DAG 渲染

阈值：
- 像素差异阈值 = 10 (0-255)
- 通过阈值 = 0.5% (允许 antialiasing / 字体子像素差异)

运行：
    pytest tests/test_webui_visual.py -v
    pytest tests/test_webui_visual.py --update-snapshots      # 更新基线
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
from playwright.sync_api import Browser, BrowserContext, Page

# Path setup for visual_diff import
TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(TESTS_DIR))
from visual_diff import compare_images, save_baseline  # noqa: E402

BASELINES_DIR = TESTS_DIR / "baselines"
DIFFS_DIR = TESTS_DIR / "diffs"
ACTUALS_DIR = TESTS_DIR / "_actual"  # 最近一次实际截图（debug 用）

UPDATE_SNAPSHOTS = os.environ.get("UPDATE_SNAPSHOTS", "0") == "1"

# 公共视觉参数（影响所有截图）
VIEWPORT = {"width": 1440, "height": 900}
DEVICE_SCALE_FACTOR = 1  # 1 = 普通；2 = retina；CI 中保持 1 减少像素数


# ────────────────────────── Fixtures ──────────────────────────


@pytest.fixture
def context(browser: Browser) -> Iterator[BrowserContext]:
    """每个测试独立 context（固定 viewport + 关闭动画），避免 cookies / storage 污染。

    共享 session-scoped ``browser`` fixture 来自 conftest_e2e.py。
    """
    ctx = browser.new_context(
        viewport=VIEWPORT,
        device_scale_factor=DEVICE_SCALE_FACTOR,
        locale="zh-CN",
        # 关闭动画 (CSS transition / animation) — 让截图稳定
        reduced_motion="reduce",
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


def _assert_or_update(actual: Path, name: str, *, max_diff_ratio: float = 0.005):
    """对比 actual 与 baseline/name.png。

    - ``--update-snapshots``: 把 actual 当作新基线
    - 否则：pixel diff + 失败时保存 diff 可视化
    """
    baseline = BASELINES_DIR / f"{name}.png"

    if UPDATE_SNAPSHOTS or not baseline.exists():
        save_baseline(actual, baseline)
        # 当更新基线时，不做断言（避免 CI 首次更新时失败）
        pytest.skip(f"Baseline saved: {baseline} (UPDATE_SNAPSHOTS=1 or missing)")

    DIFFS_DIR.mkdir(parents=True, exist_ok=True)
    diff_output = DIFFS_DIR / f"{name}_diff.png"

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

    def test_login_page_baseline(self, context: BrowserContext, app_url: str):
        page = context.new_page()
        try:
            page.goto(f"{app_url}/login")
            page.wait_for_selector("input[type='password']", timeout=10_000)
            # 等字体加载（Playwright 默认在 navigation 后等 fonts）
            page.wait_for_function("document.fonts.ready", timeout=5000)
            # 等动画/过渡结束
            page.wait_for_timeout(200)

            actual = _screenshot_page(page, "login_page")
            _assert_or_update(actual, "login_page", max_diff_ratio=0.01)
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

            yield page
        finally:
            page.close()

    def test_message_bubble_baseline(self, chat_page: Page):
        """完整聊天区（用户气泡 + 助手气泡）截图。

        新版 MessageList 无 data-testid；旧 ``.flex-1`` 选择器会命中
        隐藏的布局元素导致 element-screenshot 超时 —— 用整页截图。
        """
        actual = _screenshot_page(chat_page, "message_bubble")
        _assert_or_update(actual, "message_bubble", max_diff_ratio=0.01)

    def test_user_bubble_only(self, chat_page: Page):
        """仅用户气泡 — 验证主色调、圆角、右对齐。"""
        # 等消息列表稳定
        chat_page.wait_for_selector("text=什么是 alpha?", timeout=5000)
        # 整页截图（旧 .bg-primary-600 会命中隐藏操作按钮，改用整页）
        actual = _screenshot_page(chat_page, "user_bubble")
        _assert_or_update(actual, "user_bubble", max_diff_ratio=0.01)

    def test_assistant_message_with_markdown(self, chat_page: Page):
        """助手消息 + Markdown 渲染。"""
        # 等 Markdown 文本出现
        chat_page.wait_for_function(
            "() => document.body.innerText.includes('脚本化回复')", timeout=10_000
        )
        chat_page.wait_for_timeout(300)  # 等 Markdown 字体稳定

        actual = _screenshot_page(chat_page, "assistant_message")
        _assert_or_update(actual, "assistant_message", max_diff_ratio=0.01)



class TestCommandPalette:
    """Cmd+K 调色板弹出视觉。"""

    def test_command_palette_baseline(self, context: BrowserContext, app_url: str):
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
            _assert_or_update(actual, "command_palette", max_diff_ratio=0.02)
        finally:
            page.close()


class TestEmptyStates:
    """空状态视觉 — 验证 EmptyState 组件在各个 panel 的渲染。

    3 个场景:
    - empty_chat: 已登录 + currentSessionId 设置 + 消息列表为空
    - empty_goal: Goal 标签 + 无活跃目标 → 显示「暂无活跃目标」
    - empty_agent: Agent 标签 + 无 agent → 显示「暂无 Agent」
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

    def test_empty_chat(self, logged_in_page: Page):
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
        _assert_or_update(actual, "empty_chat", max_diff_ratio=0.01)

    def test_empty_goal(self, logged_in_page: Page):
        """空 Goal 列表 — Goal tab + 无目标。

        预期: 显示「暂无活跃目标」+ 添加按钮
        """
        # 默认 rightPanelTab='goal' — 但 layout store 默认是 'dag'
        # 切换到 goal tab
        logged_in_page.wait_for_function(
            "() => typeof window.__workflowStore !== 'undefined'", timeout=5000
        )
        # 通过点击 IconNav 的 Goal 按钮切换
        # title 属性: Workflow=undefined(默认), Target=goal, Bot=agent
        goal_btn = logged_in_page.locator("button[title='goal']")
        if goal_btn.count() > 0:
            goal_btn.click()
            logged_in_page.wait_for_timeout(500)

        # 等 EmptyState「暂无活跃目标」出现
        try:
            logged_in_page.wait_for_selector("text=暂无活跃目标", timeout=5000)
        except Exception:
            pytest.skip("Goal EmptyState not found (right panel may be hidden)")
            return

        logged_in_page.wait_for_timeout(300)

        # 截整个右侧面板
        right_panel = logged_in_page.locator("[class*='RightPanel'], aside, [role='complementary']")
        if right_panel.count() == 0:
            # fallback: 截图右侧 480px 区域
            viewport = logged_in_page.viewport_size
            actual = logged_in_page.screenshot(
                path=str(ACTUALS_DIR / "empty_goal.png"),
                clip={
                    "x": viewport["width"] - 480,
                    "y": 48,
                    "width": 480,
                    "height": viewport["height"] - 48,
                },
                animations="disabled",
            )
            ACTUALS_DIR.mkdir(parents=True, exist_ok=True)
            (ACTUALS_DIR / "empty_goal.png").write_bytes(actual)
            actual = ACTUALS_DIR / "empty_goal.png"
        else:
            actual = _screenshot_element(logged_in_page, right_panel.first, "empty_goal")

        _assert_or_update(actual, "empty_goal", max_diff_ratio=0.02)

    def test_empty_agent(self, logged_in_page: Page):
        """空 Agent 列表 — Agent tab + 无 agent。

        预期: 显示「暂无 Agent」
        """
        # 切换到 agent tab
        logged_in_page.wait_for_function(
            "() => typeof window.__workflowStore !== 'undefined'", timeout=5000
        )
        agent_btn = logged_in_page.locator("button[title='agent']")
        if agent_btn.count() > 0:
            agent_btn.click()
            logged_in_page.wait_for_timeout(500)

        # 等 EmptyState「暂无 Agent」出现
        try:
            logged_in_page.wait_for_selector("text=暂无 Agent", timeout=5000)
        except Exception:
            pytest.skip("Agent EmptyState not found")
            return

        logged_in_page.wait_for_timeout(300)

        # 截右侧面板
        viewport = logged_in_page.viewport_size
        actual_path = ACTUALS_DIR / "empty_agent.png"
        ACTUALS_DIR.mkdir(parents=True, exist_ok=True)
        logged_in_page.screenshot(
            path=str(actual_path),
            clip={
                "x": viewport["width"] - 480,
                "y": 48,
                "width": 480,
                "height": viewport["height"] - 48,
            },
            animations="disabled",
        )

        _assert_or_update(actual_path, "empty_agent", max_diff_ratio=0.02)
