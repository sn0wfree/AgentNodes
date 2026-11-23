"""Comprehensive Playwright E2E test for QuantNodes-Research.

Tests the full workflow:
1. Login
2. Create session
3. Factor analysis conversation
4. Strategy creation
5. Goal system interaction
6. Verify UI displays correctly
7. Leave session with complete history
"""

from __future__ import annotations

import asyncio
import os
import time

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
    """Point this module at the self-started TEST_MODE backend."""
    global BASE_URL
    BASE_URL = backend_server["base_url"]


async def _login(page):
    """Login via UI and return JWT token."""
    await page.goto(f"{BASE_URL}/login", timeout=60000)
    await page.wait_for_load_state("networkidle")

    username_input = page.locator('input[placeholder*="用户"], input[type="text"]').first
    password_input = page.locator('input[type="password"]').first
    await username_input.fill("admin")
    await password_input.fill("admin")

    login_btn = page.locator('button:has-text("登录"), button[type="submit"]').first
    await login_btn.click()

    await page.wait_for_url("**/chat", timeout=10000)
    await page.wait_for_load_state("networkidle")
    await page.goto(f"{BASE_URL}/chat")
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(2000)

    token = await page.evaluate("""() => {
        const raw = localStorage.getItem('sr-auth');
        if (raw) {
            const parsed = JSON.parse(raw);
            return parsed.state?.token || parsed.token || '';
        }
        return '';
    }""")
    return token


async def _create_session(page):
    """Create a new chat session via UI."""
    plus_btn = page.locator("button").filter(has_text="+").first
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


async def _wait_for_response(page, timeout_s=60):
    """Wait for assistant response to complete.

    **返回 False = 超时**（调用方**必须**检查——R-017：并行下失败的测试先
    判「隔离」还是「真实缺陷」，而「超时未被检查」会让断言在别处炸出，
    看起来像业务缺陷）。

    **超时基线**：单跑本文件 `test_full_session_history` 约 19s（3 轮消息
    + 响应，每轮响应 ~5-6s），60s 超时单跑绰绰有余。但**全量并行**
    （`-n 12`，12 个 worker 抢 12 核）时真实 LLM 调用会显著变慢——2026-11-12
    实测该测试在此条件下约 1/4 概率超时，导致「期望 ≥3 条 user 消息，实得
    2 条」的**假失败**（消息尚未持久化就 reload 了）。

    因此：默认超时**按并行场景**取值，并允许调用方覆盖。
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        body = await page.inner_text("body")
        if ("Agent" in body and len(body) > 200) or "agent_done" in body.lower():
            await page.wait_for_timeout(3000)
            return True
        agent_msgs = page.locator('[class*="assistant"], [class*="Agent"]')
        count = await agent_msgs.count()
        if count > 0:
            text = await agent_msgs.last.inner_text()
            if len(text.strip()) > 10:
                await page.wait_for_timeout(3000)
                return True
        await page.wait_for_timeout(500)
    print(
        f"   [WARN] _wait_for_response 超时（{timeout_s}s）——"
        f"并发下 LLM 变慢属环境因素，断言消息条数前应视为「未完成」"
    )
    return False


async def _wait_for_response_or_fail(page, timeout_s:60, *, what: str = "响应"):
    """等待响应；超时**显式失败**（带可诊断信息）。

    用于「后续断言依赖这次响应已完成」的场景——超时时继续走会让断言在
    别处炸出（如「实得 2 条消息」），把环境问题伪装成业务缺陷。
    """
    responded = await _wait_for_response(page, timeout_s=timeout_s)
    assert responded, (
        f"等待{what}超时（{timeout_s}s）——并发下 LLM 变慢属环境因素；"
        f"请单独重跑该测试确认是 flaky 而非回归"
    )
    return responded


async def _take_screenshot(page, name):
    """Take a screenshot with timestamp."""
    path = f"/tmp/e2e_{name}_{int(time.time())}.png"
    # timeout 显式给 60s：full_page 截图在 8 worker 并行下偶发超过 playwright
    # 默认 30s（曾把 test_goal_system_workflow / test_full_session_history
    # 整个拖红；单跑 16s 通过）—— 那是资源竞争，不是被测行为失败。
    await page.screenshot(path=path, full_page=True, timeout=60_000)
    print(f"📸 Screenshot: {path}")
    return path


@pytest.mark.asyncio
async def test_factor_analysis_workflow():
    """E2E: Complete factor analysis workflow via chat."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        try:
            # 1. Login
            print("1. Logging in...")
            await _login(page)
            await _take_screenshot(page, "01_login")

            # 2. Create session
            print("2. Creating session...")
            await _create_session(page)
            await page.wait_for_timeout(1000)
            await _take_screenshot(page, "02_session_created")

            # 3. Send factor analysis request
            print("3. Sending factor analysis request...")
            await _send_message(page, "帮我分析20日动量因子在A股的IC表现")
            await page.wait_for_timeout(2000)
            await _take_screenshot(page, "03_message_sent")

            # 4. Wait for response
            print("4. Waiting for response...")
            responded = await _wait_for_response(page, timeout_s=60)
            assert responded, "No assistant response received"
            await _take_screenshot(page, "04_response_received")

            # 5. Verify message display
            print("5. Verifying message display...")
            await page.inner_text("body")

            # Check user message exists
            user_msgs = page.locator('[class*="bg-primary"][class*="rounded-lg"]')
            user_count = await user_msgs.count()
            print(f"   User messages: {user_count}")
            assert user_count >= 1, "No user messages found"

            # Check assistant message exists
            agent_labels = page.locator('text="Agent"')
            agent_count = await agent_labels.count()
            print(f"   Agent messages: {agent_count}")
            assert agent_count >= 1, "No agent messages found"

            # Check for thinking block
            thinking_blocks = page.locator('[class*="violet"], [class*="thinking"]')
            thinking_count = await thinking_blocks.count()
            print(f"   Thinking blocks: {thinking_count}")

            await _take_screenshot(page, "05_verified")

            print("✅ Factor analysis workflow completed successfully!")

        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_strategy_creation_workflow():
    """E2E: Strategy creation via chat."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        try:
            # 1. Login
            await _login(page)

            # 2. Create session
            await _create_session(page)
            await page.wait_for_timeout(1000)

            # 3. Send strategy creation request
            print("Sending strategy creation request...")
            await _send_message(page, "基于20日动量因子创建一个策略，调仓频率5天，做多top10")
            await page.wait_for_timeout(2000)
            await _take_screenshot(page, "strategy_request")

            # 4. Wait for response
            responded = await _wait_for_response(page, timeout_s=60)
            assert responded, "No response"

            # 5. Verify strategy files mentioned
            body = await page.inner_text("body")
            assert "strategy" in body.lower() or "策略" in body, "Strategy not mentioned"

            await _take_screenshot(page, "strategy_complete")
            print("✅ Strategy creation workflow completed!")

        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_goal_system_workflow():
    """E2E: Goal system interaction via chat."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        try:
            # 1. Login
            await _login(page)

            # 2. Create session
            await _create_session(page)
            await page.wait_for_timeout(1000)

            # 3. Send goal-oriented request
            print("Sending goal-oriented request...")
            await _send_message(page, "分析20日动量因子IC，保存分析结果，然后创建策略并回测")
            await page.wait_for_timeout(2000)
            await _take_screenshot(page, "goal_request")

            # 4. Wait for response
            responded = await _wait_for_response(page, timeout_s=90)
            assert responded, "No response"

            await _take_screenshot(page, "goal_complete")
            print("✅ Goal system workflow completed!")

        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_cancel_functionality():
    """E2E: Test cancel/interrupt functionality."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        try:
            # 1. Login
            await _login(page)

            # 2. Create session
            await _create_session(page)
            await page.wait_for_timeout(1000)

            # 3. Send a message
            await _send_message(page, "你好")
            await page.wait_for_timeout(2000)

            # 4. Check if cancel button appears during streaming
            cancel_btn = page.locator('button:has(svg.lucide-square)')
            is_visible = await cancel_btn.is_visible(timeout=5000)
            print(f"Cancel button visible during streaming: {is_visible}")

            # 5. Wait for response
            await _wait_for_response_or_fail(page, timeout_s=60, what="取消测试的响应")

            await _take_screenshot(page, "cancel_test")
            print("✅ Cancel functionality test completed!")

        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_full_session_history():
    """E2E: Complete session with multiple messages for review."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        try:
            # 1. Login
            print("1. Logging in...")
            await _login(page)

            # 2. Create session
            print("2. Creating session...")
            await _create_session(page)
            await page.wait_for_timeout(1000)

            # 3. Message 1: Greeting
            print("3. Sending greeting...")
            await _send_message(page, "你好，我想研究A股的动量因子")
            await _wait_for_response_or_fail(page, timeout_s=120, what="第 1 条响应")
            await _take_screenshot(page, "full_01_greeting")

            # 4. Message 2: Factor analysis
            print("4. Requesting factor analysis...")
            await _send_message(page, "帮我分析20日动量因子在A股的IC表现")
            await _wait_for_response_or_fail(page, timeout_s=120, what="第 2 条响应")
            await _take_screenshot(page, "full_02_analysis")

            # 5. Message 3: Strategy creation
            print("5. Requesting strategy creation...")
            await _send_message(page, "基于这个因子创建一个策略配置")
            await _wait_for_response_or_fail(page, timeout_s=120, what="第 3 条响应")
            await _take_screenshot(page, "full_03_strategy")

            # 6. Verify all messages are displayed
            print("6. Verifying message history...")

            # Reload so the Virtuoso virtualized list renders all
            # persisted messages (live rendering only keeps a window)
            await page.reload()
            await page.wait_for_load_state("domcontentloaded")

            # Check multiple user messages (gradient bubble)
            user_msgs = page.locator('div.rounded-br-md.bg-gradient-to-br')

            # **轮询等消息渲染完**（不再固定 sleep 3s）：并发下后端持久化 +
            # 前端虚拟列表渲染都更慢，固定等待会读到「还没渲染完」的中间态
            # → 误报「实得 2 条」（2026-11-12 实测的 flaky 根因之一）。
            # 超时后**不直接失败**——下面的 count 断言会给出更具体的信息。
            deadline = asyncio.get_event_loop().time() + 30
            user_count = 0
            while asyncio.get_event_loop().time() < deadline:
                user_count = await user_msgs.count()
                if user_count >= 3:
                    break
                await page.wait_for_timeout(500)
            print(f"   Total user messages: {user_count}")
            assert user_count >= 3, (
                f"Expected ≥3 user messages, got {user_count}（已轮询等待 30s；"
                f"若单跑通过则为并发下的环境时序问题）"
            )

            # Check multiple assistant messages
            agent_labels = page.locator('text="Agent"')
            agent_count = await agent_labels.count()
            print(f"   Total agent messages: {agent_count}")
            assert agent_count >= 3, f"Expected ≥3 agent messages, got {agent_count}"

            # 7. Take final screenshot
            await _take_screenshot(page, "full_04_complete")

            # 8. Print session summary
            print("\n" + "="*60)
            print("SESSION COMPLETE - Full history available for review")
            print("="*60)
            print(f"User messages: {user_count}")
            print(f"Agent messages: {agent_count}")
            print("Screenshots saved to /tmp/e2e_full_*")
            print("="*60)

            print("✅ Full session history test completed!")

        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_tool_call_display():
    """E2E: Verify tool calls are displayed correctly in UI."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        try:
            # 1. Login
            await _login(page)

            # 2. Create session
            await _create_session(page)
            await page.wait_for_timeout(1000)

            # 3. Send message that triggers tool calls
            print("Sending message that triggers tool calls...")
            await _send_message(page, "列出可用的数据源")
            await _wait_for_response_or_fail(page, timeout_s=120, what="工具调用响应")

            # 4. Check for tool call display
            tool_blocks = page.locator('[class*="tool"], [class*="ToolCall"]')
            tool_count = await tool_blocks.count()
            print(f"Tool call blocks: {tool_count}")

            await _take_screenshot(page, "tool_calls")

            # 5. Verify tool call content
            body = await page.inner_text("body")
            # Tool calls should show function names
            assert "list_data_sources" in body or "数据源" in body, "Tool call not displayed"

            print("✅ Tool call display test completed!")

        finally:
            await browser.close()
