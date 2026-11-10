"""E2E test: send multiple messages and verify none disappear."""
import asyncio

import pytest
# E2E 是 opt-in 车道：playwright 未安装时整个模块降级为 skip（而非
# 收集错误——那会让 batch_gate 的 collect 锚点多出 5 个假错误）。
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
    await page.goto(f"{BASE_URL}/login")
    await page.wait_for_load_state("networkidle")
    await page.locator('input[type="text"]').first.fill("admin")
    await page.locator('input[type="password"]').first.fill("admin")
    await page.locator('button:has-text("登录")').first.click()
    await page.wait_for_url("**/", timeout=10000)
    await page.wait_for_load_state("networkidle")
    # Navigate into the chat page explicitly (login now lands on
    # the monitor home page)
    await page.goto(f"{BASE_URL}/chat")
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(2000)


async def _create_session(page):
    btn = page.locator('button[title="新建会话"]').first
    if await btn.is_visible(timeout=3000):
        await btn.click()
        await page.wait_for_timeout(1000)


async def _send(page, text):
    # Wait for the composer to be writable (disabled while streaming)
    await page.locator("textarea").first.wait_for(state="visible", timeout=15000)
    await page.wait_for_timeout(500)
    if await page.locator("textarea").first.is_disabled():
        # Still streaming — wait for the stop button to disappear
        await page.wait_for_function(
            "() => !document.querySelector('textarea').disabled",
            timeout=15000,
        )
    await page.locator("textarea").first.fill(text)
    await page.wait_for_timeout(300)
    await page.locator("button:has(svg.lucide-send)").first.click()
    # Wait until the composer is cleared (message accepted)
    await page.wait_for_function(
        "() => document.querySelector('textarea').value === ''",
        timeout=10000,
    )


async def _wait_done(page, timeout_s=30):
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        body = await page.inner_text("body")
        if "Agent" in body and len(body) > 100:
            await page.wait_for_timeout(3000)
            return True
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
async def test_messages_persist_after_new_send():
    """Send 3 messages, screenshot after each, verify none disappear."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})

        try:
            await _login(page)
            await _create_session(page)

            msgs = ["记住数字 11", "记住数字 22", "数字是什么？"]

            for i, msg in enumerate(msgs):
                print(f"--- Sending msg {i+1}: {msg}")
                await _send(page, msg)
                await page.wait_for_timeout(2000)
                ok = await _wait_done(page, timeout_s=60)
                assert ok, f"msg {i+1} no response"
                await page.wait_for_timeout(1000)

                # Screenshot
                await page.screenshot(path=f"/tmp/test_persist_{i+1}.png")

            # Reload and verify all messages persisted. The message list
            # is a Virtuoso virtualized list, so counting bubbles live
            # is unreliable; the persistence contract is "survives a
            # reload" (backend + projector write path).
            await page.reload()
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(3000)

            body = await page.inner_text("body")
            for i, msg in enumerate(msgs):
                print(f"  After reload, msg{i+1} present: {msg in body}")
                assert msg in body, f"msg{i+1} {msg!r} lost after reload"

            # Bubbles must also be rendered on reload
            user_bubbles = page.locator('div.rounded-br-md.bg-gradient-to-br')
            count = await user_bubbles.count()
            print(f"  User bubbles after reload: {count}")
            assert count == len(msgs), f"Expected {len(msgs)} user bubbles after reload, got {count}"

            print("✅ All messages persisted — none disappeared")
        finally:
            await browser.close()
