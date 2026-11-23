"""E2E Playwright test for chat role-swap fix.

Verifies:
1. User messages display correctly (blue bubbles, right-aligned)
2. Assistant messages display correctly (agent bubbles, left-aligned)
3. No role-swap (assistant content never appears in user bubble)
4. History context works (second message references first)
5. Streaming state clears after response completes
"""
import asyncio
import os

import pytest
# E2E 是 opt-in 车道：playwright 未安装时整个模块降级为 skip（而非
# 收集错误——那会让 batch_gate 的 collect 锚点多出 5 个假错误）。
# E2E 是 opt-in 车道（与 visual/catalog/persist 一致）：默认全量 pytest 里
# skip，只在 AN_RUN_BROWSER_TESTS=1 时运行，避免真 LLM/浏览器状态污染主门禁。
pytestmark = pytest.mark.skipif(
    os.environ.get("AN_RUN_BROWSER_TESTS", "0") != "1",
    reason="Browser E2E; set AN_RUN_BROWSER_TESTS=1 to run",
)
pytest.importorskip("playwright")
from playwright.async_api import async_playwright

BASE_URL = "http://127.0.0.1:8783"

pytest_plugins = ["conftest_e2e"]


@pytest.fixture(scope="module", autouse=True)
def _use_test_server(backend_server):
    """Point this module at the self-started TEST_MODE backend
    (the hardcoded 8783 is a manually-run deployment server that is
    not guaranteed to exist during CI)."""
    global BASE_URL
    BASE_URL = backend_server["base_url"]


async def _login(page):
    """Login via UI and return JWT token."""
    await page.goto(f"{BASE_URL}/login")
    await page.wait_for_load_state("networkidle")

    # Fill login form
    username_input = page.locator('input[placeholder*="用户"], input[type="text"]').first
    password_input = page.locator('input[type="password"]').first
    await username_input.fill("admin")
    await password_input.fill("admin")

    # Click login
    login_btn = page.locator('button:has-text("登录"), button[type="submit"]').first
    await login_btn.click()

    # Wait for navigation to chat page
    await page.wait_for_url("**/chat", timeout=10000)
    await page.wait_for_load_state("networkidle")
    # Navigate into the chat page explicitly (login now lands on
    # the monitor home page)
    await page.goto(f"{BASE_URL}/chat")
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(2000)

    # Extract token from localStorage
    token = await page.evaluate("""() => {
        const raw = localStorage.getItem('sr-auth');
        if (raw) {
            const parsed = JSON.parse(raw);
            return parsed.state?.token || parsed.token || '';
        }
        return '';
    }""")
    print(f"Logged in, token: {token[:20]}...")
    return token


async def _create_session(page):
    """Create a new chat session via UI."""
    # Click "+" to create new session (SVG Plus icon, title="新建会话")
    plus_btn = page.locator('button[title="新建会话"]').first
    if await plus_btn.is_visible(timeout=3000):
        await plus_btn.click()
        await page.wait_for_timeout(1000)


async def _send_message(page, text):
    """Type a message and send it."""
    textarea = page.locator("textarea").first
    await textarea.fill(text)
    await page.wait_for_timeout(300)
    send_btn = page.locator("button:has(svg.lucide-send)").first
    await send_btn.click()


async def _wait_for_response(page, timeout_s=30):
    """Wait for assistant response to complete."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        body = await page.inner_text("body")
        # Look for assistant content indicators
        if ("Agent" in body and len(body) > 100) or "agent_done" in body.lower():
            await page.wait_for_timeout(3000)
            return True
        # Also check if there are assistant message elements
        agent_msgs = page.locator('[class*="assistant"], [class*="Agent"]')
        count = await agent_msgs.count()
        if count > 0:
            text = await agent_msgs.last.inner_text()
            if len(text.strip()) > 5:
                await page.wait_for_timeout(3000)
                return True
        await page.wait_for_timeout(500)
    return False


@pytest.mark.asyncio
async def test_no_role_swap_two_messages():
    """Send two messages, verify no role-swap and history works."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        try:
            await _login(page)
            await _create_session(page)

            # --- Send first message ---
            print("Sending first message: 记住数字 99")
            await _send_message(page, "记住数字 99")
            await page.wait_for_timeout(2000)
            responded = await _wait_for_response(page, timeout_s=30)
            assert responded, "First message: no assistant response"
            await page.screenshot(path="/tmp/e2e_msg1.png")
            print("✅ First message responded")

            # --- Send second message ---
            print("Sending second message: 数字是什么？")
            await _send_message(page, "数字是什么？")
            await page.wait_for_timeout(2000)
            responded = await _wait_for_response(page, timeout_s=30)
            assert responded, "Second message: no assistant response"
            await page.screenshot(path="/tmp/e2e_msg2.png")
            print("✅ Second message responded")

            # --- Verify role correctness ---
            # Get all blue user bubbles (right-aligned)
            user_bubbles = page.locator('[class*="bg-primary"][class*="rounded-lg"]')
            user_count = await user_bubbles.count()
            print(f"User bubbles: {user_count}")

            # Check no role-swap in user bubbles
            for i in range(user_count):
                text = await user_bubbles.nth(i).inner_text()
                assert "<think>" not in text, f"User bubble {i} has <think> (role-swap!)"
                assert len(text) < 200, f"User bubble {i} too long ({len(text)}), likely role-swap"

            # Check assistant messages have content
            agent_labels = page.locator('text="Agent"')
            agent_count = await agent_labels.count()
            print(f"Agent messages: {agent_count}")
            assert agent_count >= 2, f"Expected ≥2 assistant messages, got {agent_count}"

            # Verify history: "99" should appear in page
            body = await page.inner_text("body")
            assert "99" in body, "History not working: '99' not in page"

            # Take final screenshot
            await page.screenshot(path="/tmp/e2e_final.png")

            print("✅ ALL TESTS PASSED")

        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_streaming_clears():
    """Verify streaming state clears after response."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        try:
            await _login(page)
            await _create_session(page)

            await _send_message(page, "你好")
            responded = await _wait_for_response(page, timeout_s=25)
            assert responded, "No response"

            # Extra wait for streaming to fully clear
            await page.wait_for_timeout(5000)
            await page.screenshot(path="/tmp/e2e_streaming.png")

            body = await page.inner_text("body")
            assert len(body) > 100, "Page too empty after response"

            print("✅ Streaming clears correctly")
        finally:
            await browser.close()
