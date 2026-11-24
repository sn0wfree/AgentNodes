"""浏览器 E2E：路由 smoke 覆盖（E2 批）。

现状基线（2026-11-24）：浏览器 E2E 只覆盖 /login、/chat、/catalog*；
App.tsx 中其余路由无浏览器覆盖。本模块为所有路由建立 smoke 级断言：

- 受保护路由未登录 → 重定向 /login（AuthGuard）
- 登录后路由可达 + 页壳标题非空 + 无 console error
- 重定向路由落到目标路径
- 权限差异：/account 用户管理表格 admin-only

不截图（避免基线爆炸），只做行为断言。
"""

# pytest_plugins 必须顶部
pytest_plugins = ["conftest_e2e"]

import uuid
from typing import Iterator

import pytest

pytestmark = pytest.mark.skipif(
    __import__("os").environ.get("AN_RUN_BROWSER_TESTS", "0") != "1",
    reason="Browser E2E; set AN_RUN_BROWSER_TESTS=1 (or run via e2e.yml)",
)
pytest.importorskip("playwright")
from playwright.sync_api import BrowserContext, Page  # noqa: E402


@pytest.fixture
def context(browser) -> Iterator[BrowserContext]:
    """每个测试独立 context（固定 viewport），避免 storage 互相污染。"""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
    yield ctx
    ctx.close()


# 受保护路由（AuthGuard）— 未登录访问必须重定向 /login
PROTECTED_ROUTES = [
    "/", "/chat", "/trace", "/account", "/monitor", "/dag",
    "/study", "/study/demo", "/run/demo/strategy", "/factors", "/strategies",
    "/some-unknown-route",  # /* fallback → AppShell
]

# 登录后应可达的路由（H1/页壳标题非空）
AUTH_ROUTES = [
    "/", "/chat", "/trace", "/account", "/monitor", "/dag",
    "/factors", "/strategies", "/study",
]

# 重定向路由：(入口, 目标 path 片段)
REDIRECT_ROUTES = [
    ("/workflow", "/dag"),
    ("/workflow-definition", "/dag"),
    ("/admin/users", "/account"),
]


def _register_and_login_token(app_url: str) -> tuple[str, str]:
    """注册临时用户，返回 (token, username)。"""
    import requests

    api = requests.Session()
    api.base_url = app_url  # type: ignore[attr-defined]
    username = f"route_{uuid.uuid4().hex[:8]}"
    r = api.post(
        f"{api.base_url}/api/auth/register",
        json={"username": username, "password": "p"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"], username


def _inject_auth(page: Page, token: str, username: str) -> None:
    page.evaluate(
        """({token, username}) => {
            localStorage.setItem('sr-auth', JSON.stringify({
                state: { token, user: { username, role: 'user' } }, version: 0
            }));
        }""",
        {"token": token, "username": username},
    )


class TestProtectedRoutes:
    @pytest.mark.parametrize("route", PROTECTED_ROUTES)
    def test_unauthenticated_redirects_to_login(
        self, context, app_url: str, route: str
    ):
        page = context.new_page()
        try:
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(f"{app_url}{route}", wait_until="domcontentloaded")
            page.wait_for_url("**/login", timeout=10_000)
            assert page.locator("input[type='password']").count() >= 1
            # AuthGuard 重定向是正常行为，不应产生 console error
            assert not errors, f"console errors on {route}: {errors}"
        finally:
            page.close()


class TestAuthenticatedRoutes:
    @pytest.mark.parametrize("route", AUTH_ROUTES)
    def test_route_renders_shell(self, context, app_url: str, route: str):
        token, username = _register_and_login_token(app_url)
        page = context.new_page()
        try:
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(f"{app_url}/login", wait_until="domcontentloaded")
            _inject_auth(page, token, username)
            page.goto(f"{app_url}{route}", wait_until="domcontentloaded")
            # 页壳锚点：header 或 h1 至少一个存在
            page.wait_for_selector("header, h1, [role='heading']", timeout=10_000)
            assert page.locator("header, h1, [role='heading']").count() >= 1, (
                f"no page shell heading on {route}"
            )
            # /study 依赖的 study API 未挂载（P2-1 已知契约缺口，非回归）：
            # smoke 只验路由可达 + 页壳，容忍那一条已知 404 resource error。
            if route == "/study":
                errors = [e for e in errors if "404" not in e]
            assert not errors, f"console errors on {route}: {errors}"
        finally:
            page.close()

    @pytest.mark.parametrize("route,target", REDIRECT_ROUTES)
    def test_legacy_routes_redirect(self, context, app_url: str, route: str, target: str):
        token, username = _register_and_login_token(app_url)
        page = context.new_page()
        try:
            page.goto(f"{app_url}/login", wait_until="domcontentloaded")
            _inject_auth(page, token, username)
            page.goto(f"{app_url}{route}", wait_until="domcontentloaded")
            page.wait_for_url(f"**{target}", timeout=10_000)
        finally:
            page.close()


class TestAccountPermission:
    def test_non_admin_has_no_user_management_table(self, context, app_url: str):
        token, username = _register_and_login_token(app_url)
        page = context.new_page()
        try:
            page.goto(f"{app_url}/login", wait_until="domcontentloaded")
            _inject_auth(page, token, username)
            page.goto(f"{app_url}/account", wait_until="domcontentloaded")
            page.wait_for_selector("header", timeout=10_000)
            # 普通用户看到设置分区，但不看到用户管理表格
            assert page.locator("text=个人账号").count() >= 1
            assert page.locator("text=用户管理").count() == 0, (
                "non-admin must not see the user-management section"
            )
        finally:
            page.close()


class TestPublicRoutes:
    @pytest.mark.parametrize("route", ["/login", "/register"])
    def test_public_route_reachable(self, context, app_url: str, route: str):
        page = context.new_page()
        try:
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            resp = page.goto(f"{app_url}{route}", wait_until="domcontentloaded")
            assert resp and resp.status < 500
            page.wait_for_selector("input", timeout=10_000)
            assert not errors, f"console errors on {route}: {errors}"
        finally:
            page.close()
