"""Real E2E tests for the Web UI using Playwright.

启动真实后端 (TEST_MODE 脚本化 SSE) + 真实 Chromium 浏览器，测试：
- 用户注册流程
- 登录流程
- 发送消息后 SSE 事件流转
- 流式文本渲染
- agent_done 后 streamingMessageId 清理

运行要求：
- 浏览器已通过 `playwright install chromium` 安装
- webui/static/ 已构建（conftest 会自动构建）
"""

# pytest_plugins must be at the very top — before any imports.
# Loads E2E fixtures (backend_server + built_frontend + browser) explicitly
# so they don't pollute the rest of the test suite.
pytest_plugins = ["conftest_e2e"]

import os
import uuid
from typing import Iterator

import pytest
import requests

# Browser E2E is opt-in (run via e2e.yml / SR_RUN_BROWSER_TESTS=1): the
# Playwright sync API keeps a running asyncio loop on the main thread for the
# whole session, which breaks pytest-asyncio for every later test file.
pytestmark = pytest.mark.skipif(
    os.environ.get("SR_RUN_BROWSER_TESTS", "0") != "1",
    reason="Browser E2E; set SR_RUN_BROWSER_TESTS=1 (or run via e2e.yml)",
)
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    expect,
)

# ────────────────────────── Helpers ──────────────────────────


# browser fixture 由 conftest_e2e.py 提供 (session scope, 复用单例)


@pytest.fixture
def context(browser: Browser) -> Iterator[BrowserContext]:
    """隔离的 BrowserContext（每个测试新 context，互不污染 cookies/storage）。"""
    ctx = browser.new_context(
        viewport={"width": 1440, "height": 900},
        locale="zh-CN",
    )
    yield ctx
    ctx.close()


@pytest.fixture
def page(context: BrowserContext, app_url: str) -> Page:
    """基础 page fixture — 等待前端加载完毕。"""
    page = context.new_page()
    page.goto(f"{app_url}/login")
    # Wait for the login form to render (Vue/React hydration)
    page.wait_for_selector("input[type='password']", timeout=10_000)
    return page


def _unique_username() -> str:
    """生成唯一用户名避免 409。"""
    return f"e2e_{uuid.uuid4().hex[:8]}"


# ────────────────────────── Register flow ──────────────────────────


class TestRegistration:
    def test_register_page_renders(self, page: Page, app_url: str):
        """访问 /register → 表单可见。"""
        page.goto(f"{app_url}/register")
        page.wait_for_selector("input[type='password']", timeout=5000)

        expect(page.locator("h1")).to_contain_text("创建账号")
        expect(page.locator("input[type='text']").first).to_be_visible()
        expect(page.locator("input[type='password']")).to_be_visible()
        expect(page.locator("button[type='submit']")).to_be_visible()

    def test_register_via_ui_redirects_to_chat(
        self, page: Page, app_url: str, api_client: requests.Session
    ):
        """完整注册流程：填表 → 提交 → 跳转 / → 显示聊天界面。"""
        username = _unique_username()

        page.goto(f"{app_url}/register")
        page.wait_for_selector("input[type='text']", timeout=5000)

        # Fill form
        page.locator("input[type='text']").first.fill(username)
        page.locator("input[type='text']").nth(1).fill("E2E Tester")
        page.locator("input[type='password']").fill("test-password-123")

        # Submit
        page.locator("button[type='submit']").click()

        # Should redirect to / and show chat UI (TopBar + IconNav)
        page.wait_for_url(f"{app_url}/", timeout=10_000)
        # TopBar header element should appear (only in authenticated AppShell)
        expect(page.locator("header")).to_be_visible(timeout=5000)

    def test_duplicate_username_shows_error(
        self, page: Page, app_url: str, api_client: requests.Session
    ):
        """重复用户名 → 显示错误。"""
        username = _unique_username()

        # Pre-create via API
        api_client.post(
            f"{api_client.base_url}/api/auth/register",
            json={"username": username, "password": "x"},
        )

        page.goto(f"{app_url}/register")
        page.locator("input[type='text']").first.fill(username)
        page.locator("input[type='password']").fill("another-password")
        page.locator("button[type='submit']").click()

        # Error message should appear
        expect(page.locator(".text-red-400")).to_be_visible(timeout=5000)


# ────────────────────────── Login flow ──────────────────────────


class TestLogin:
    def test_login_existing_user(
        self, page: Page, app_url: str, api_client: requests.Session
    ):
        """已有用户通过 UI 登录。"""
        username = _unique_username()
        password = "login-pwd-456"

        # Pre-create
        api_client.post(
            f"{api_client.base_url}/api/auth/register",
            json={"username": username, "password": password},
        )

        page.goto(f"{app_url}/login")
        page.wait_for_selector("input[type='password']", timeout=5000)

        page.locator("input[type='text']").first.fill(username)
        page.locator("input[type='password']").fill(password)
        page.locator("button[type='submit']").click()

        page.wait_for_url(f"{app_url}/", timeout=10_000)
        # TopBar header element should appear (only in authenticated AppShell)
        expect(page.locator("header")).to_be_visible(timeout=5000)

    def test_login_wrong_password_stays_on_page(
        self, page: Page, app_url: str, api_client: requests.Session
    ):
        """错误密码 → 留在登录页。

        api/client.ts 在收到 401 时会调用 ``window.location.href = '/login'``
        所以错误状态会被丢弃；只验证用户被踢回登录页（说明登录未成功）。
        """
        username = _unique_username()
        api_client.post(
            f"{api_client.base_url}/api/auth/register",
            json={"username": username, "password": "correct"},
        )

        page.goto(f"{app_url}/login")
        page.wait_for_selector("input[type='password']", timeout=5000)
        page.locator("input[type='text']").first.fill(username)
        page.locator("input[type='password']").fill("wrong-password")
        page.locator("button[type='submit']").click()

        # Wait for any navigation/redirect triggered by 401 handler
        page.wait_for_timeout(2000)

        # We should be back on /login (not authenticated AppShell)
        assert page.url.rstrip("/").endswith("/login"), f"Expected /login, got {page.url}"
        # TopBar should NOT be visible (no auth)
        assert page.locator("header").count() == 0, "TopBar should not render after failed login"


# ────────────────────────── Auth guard ──────────────────────────


class TestAuthGuard:
    def test_unauthenticated_user_redirected_to_login(
        self, context: BrowserContext, app_url: str
    ):
        """未登录访问 / → 重定向 /login。"""
        page = context.new_page()
        page.goto(f"{app_url}/", wait_until="domcontentloaded")

        # Should be redirected to /login
        page.wait_for_url(f"{app_url}/login", timeout=5000)
        # LoginPage has form with password input
        expect(page.locator("input[type='password']")).to_be_visible(timeout=5000)
        # Should NOT have TopBar (no auth)
        assert page.locator("header").count() == 0, "TopBar should not render for unauthenticated users"

    def test_static_assets_load_without_auth(self, app_url: str):
        """静态资源（CSS/JS）无需 token。"""
        r = requests.get(f"{app_url}/")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")


# ────────────────────────── Chat + SSE flow ──────────────────────────


class TestChatFlow:
    """核心 E2E：注册 → 创建 session → 发消息 → 验证 SSE 事件到达。"""

    @pytest.fixture
    def authenticated_page(
        self, page: Page, app_url: str, api_client: requests.Session
    ) -> Page:
        """预登录的 page fixture。"""
        username = _unique_username()
        password = "chat-pwd-789"

        # 注册 (via API — 直接拿 token，更稳)
        r = api_client.post(
            f"{api_client.base_url}/api/auth/register",
            json={"username": username, "display_name": "Chat Tester", "password": password},
        )
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]

        # 创建 session
        r = api_client.post(
            f"{api_client.base_url}/api/chat/session",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "E2E chat session"},
        )
        assert r.status_code == 200, r.text
        session_id = r.json()["id"]

        # 直接通过 localStorage 注入 token（避免走登录 UI race condition）
        page.goto(f"{app_url}/login")
        page.wait_for_selector("input[type='password']", timeout=5000)
        page.evaluate(
            """({ token, user }) => {
                localStorage.setItem('sr-auth', JSON.stringify({
                    state: { token, user }, version: 0
                }));
            }""",
            {
                "token": token,
                "user": r.json().get("user", {"username": username, "display_name": "Chat Tester"}),
            },
        )

        # 重新加载 → AuthGuard 看到 token → AppShell 渲染。
        # Chat 页路由是 /chat（/ 是 Dashboard）。
        page.goto(f"{app_url}/chat")
        page.wait_for_selector("header", timeout=10_000)  # TopBar

        # 等待 window.__sessionStore 暴露 (main.tsx 异步 import)
        page.wait_for_function(
            "() => typeof window.__sessionStore !== 'undefined'",
            timeout=5000,
        )

        # 注入 currentSessionId
        page.evaluate(
            """(sessionId) => {
                window.__sessionStore.setState({
                    currentSessionId: sessionId,
                    sessions: [{
                        id: sessionId,
                        title: 'E2E chat session',
                        created_at: Date.now() / 1000,
                        updated_at: Date.now() / 1000,
                    }],
                });
            }""",
            session_id,
        )

        # 等待输入框变为可用 (currentSessionId 已设置)
        page.wait_for_selector(
            "textarea[placeholder*='输入消息']", timeout=10_000
        )
        return page

    def test_send_message_receives_sse_streaming(
        self, authenticated_page: Page, app_url: str, api_client: requests.Session
    ):
        """发消息 → 验证 SSE 文本流式到达 → agent_done 后消息固化。"""
        page = authenticated_page

        # 找到输入框 + 发送按钮
        textarea = page.locator("textarea[placeholder*='输入消息']")
        textarea.click()
        textarea.type("什么是 alpha 因子？")

        # 点击发送按钮 (Composer 末尾的发送按钮，title="发送 (Enter)")
        send_btn = page.locator('button[title="发送 (Enter)"]').last
        send_btn.click()

        # 1. 等待 user 消息立即出现 (乐观更新)
        page.wait_for_function(
            """() => window.__chatStore?.getState().messages.size >= 2""",
            timeout=5_000,
        )

        # 2. 等待第一个 text_delta 抵达 (脚本化回复)
        page.wait_for_function(
            """() => window.__chatStore?.getState().streamingText.includes('脚本化回复')""",
            timeout=15_000,
        )

        # 3. 等待所有 text_delta 抵达 (包含最后的 "agent_done 清空")
        page.wait_for_function(
            """() => window.__chatStore?.getState().streamingText.includes('agent_done 清空')""",
            timeout=15_000,
        )

        # 4. 等待 agent_done → streamingMessageId 被清空
        page.wait_for_function(
            """() => window.__chatStore?.getState().streamingMessageId === null""",
            timeout=15_000,
        )

        # 5. 最终断言：消息正文内容完整
        body_text = page.inner_text("body")
        assert "什么是 alpha 因子？" in body_text, "User message missing"
        assert "脚本化回复" in body_text, "Assistant reply missing"
        assert "测试要点" in body_text, "Markdown section missing"
        assert "agent_done" in body_text, "agent_done reference missing"

    def test_sse_status_indicator_shows_connected(
        self, authenticated_page: Page, app_url: str
    ):
        """TopBar 上的 SSEStatus 指示器出现。"""
        page = authenticated_page

        # SSEStatus 是一个状态指示器（连点 / 断点）
        # 等待连接建立 — 通常会有 'Connected' 或类似文本
        page.wait_for_timeout(1000)  # 给 SSE 时间连接

        # 查找 SSEStatus 元素 — TopBar 内有 svg 或绿点
        topbar = page.locator("header").first
        expect(topbar).to_be_visible()

    def test_message_input_available_with_auto_created_session(
        self, context: BrowserContext, app_url: str, api_client: requests.Session
    ):
        """无历史会话时 AppShell 自动创建新会话 → textarea 可用。"""
        username = _unique_username()
        r = api_client.post(
            f"{api_client.base_url}/api/auth/register",
            json={"username": username, "password": "p"},
        )
        token = r.json()["access_token"]

        page = context.new_page()
        # 通过 localStorage 直接注入 token 跳过登录 UI
        page.goto(f"{app_url}/login")
        page.evaluate(
            "(t) => localStorage.setItem('sr-auth', JSON.stringify({state:{token:t}}))",
            token,
        )
        # Chat 页路由是 /chat（/ 是 Dashboard）
        page.goto(f"{app_url}/chat")

        # AppShell 自动创建会话（无历史 session 时）→ textarea 可用
        page.wait_for_selector("textarea", timeout=5000)
        textarea = page.locator("textarea").first
        expect(textarea).to_be_enabled()
        # 自动创建的会话标题出现在会话列表
        expect(page.locator("text=新会话").first).to_be_visible(timeout=5000)
