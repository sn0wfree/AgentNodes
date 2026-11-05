"""Catalog visual regression tests — Storybook-style component index.

每个 story 都有独立的 URL 和 isolated 渲染。
/catalog         → index 页面（所有 story 卡片）
/catalog/:name   → 单个 story（隔离 stage 内渲染组件）

这是更细粒度的视觉回归：每个组件的状态/变体都有独立基线。

CI 集成:
- `./install.sh --record-baselines` 会重录所有 visual baselines (含 catalog)
- 失败时 diff 图保存到 tests/diffs/
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
from playwright.sync_api import BrowserContext, Page

# Path setup for visual_diff import
TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(TESTS_DIR))
from visual_diff import compare_images, save_baseline  # noqa: E402

BASELINES_DIR = TESTS_DIR / "baselines"
ACTUALS_DIR = TESTS_DIR / "_actual"
DIFFS_DIR = TESTS_DIR / "diffs"
UPDATE_SNAPSHOTS = os.environ.get("UPDATE_SNAPSHOTS", "0") == "1"

VIEWPORT = {"width": 1440, "height": 900}
DEVICE_SCALE_FACTOR = 1


# ────────────────────────── Fixtures ──────────────────────────


@pytest.fixture
def context(browser) -> Iterator[BrowserContext]:
    """独立 context，固定 viewport + 关动画。"""
    ctx = browser.new_context(
        viewport=VIEWPORT,
        device_scale_factor=DEVICE_SCALE_FACTOR,
        locale="zh-CN",
        reduced_motion="reduce",
    )
    yield ctx
    ctx.close()


# ────────────────────────── Helpers ──────────────────────────


def _screenshot_stage(page: Page, name: str) -> Path:
    """截取 catalog stage 区域（隔离的组件渲染容器）。"""
    ACTUALS_DIR.mkdir(parents=True, exist_ok=True)
    actual = ACTUALS_DIR / f"{name}.png"
    stage = page.locator('[data-testid="catalog-stage"]')
    stage.wait_for(state="visible", timeout=10_000)
    stage.screenshot(path=str(actual), animations="disabled")
    return actual


def _screenshot_page(page: Page, name: str) -> Path:
    """截取整个 viewport。"""
    ACTUALS_DIR.mkdir(parents=True, exist_ok=True)
    actual = ACTUALS_DIR / f"{name}.png"
    page.screenshot(path=str(actual), full_page=False, animations="disabled")
    return actual


def _assert_or_update(actual: Path, name: str, *, max_diff_ratio: float = 0.005):
    """对比 actual 与 baseline/name.png。失败时保存 diff 可视化。"""
    baseline = BASELINES_DIR / f"{name}.png"

    if UPDATE_SNAPSHOTS or not baseline.exists():
        save_baseline(actual, baseline)
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

    pct = result.diff_ratio * 100
    msg = (
        f"Visual regression (catalog): {name}\n"
        f"  diff pixels: {result.diff_pixel_count:,} / {result.total_pixels:,} "
        f"({pct:.2f}% > {max_diff_ratio * 100:.2f}%)\n"
        f"  baseline: {baseline}\n"
        f"  actual:   {actual}\n"
        f"  diff img: {diff_output}"
    )
    raise AssertionError(msg)


# ────────────────────────── Tests ──────────────────────────


class TestCatalogIndex:
    """Catalog 首页（/catalog）— 故事卡片网格。"""

    def test_catalog_index_baseline(self, context: BrowserContext, app_url: str):
        page = context.new_page()
        try:
            page.goto(f"{app_url}/catalog")
            page.wait_for_selector('a[data-testid^="catalog-card"]', timeout=10_000)
            # 等字体 + 全部卡片渲染
            page.wait_for_function("document.fonts.ready", timeout=5000)
            page.wait_for_timeout(300)

            actual = _screenshot_page(page, "catalog_index")
            _assert_or_update(actual, "catalog_index", max_diff_ratio=0.01)
        finally:
            page.close()


# 所有 story 名称从 src/catalog/stories.tsx 提取
# 这里硬编码以避免 import React 组件（vite-only 模块不能直接 import）
STORY_NAMES = [
    # common
    "badge-default",
    "spinner",
    "empty-state",
    "skeleton",
    "nav-popover",
    "confirm-dialog-default",
    "confirm-dialog-danger",
    "command-palette",
    # chat
    "message-bubble-user",
    "assistant-message",
    "streaming-text",
    "streaming-text-done",
    "markdown-renderer",
    "tool-call-running",
    "tool-call-done",
    "tool-call-error",
    "file-edit-block",
    "table-block",
    "chart-bar",
    "chart-line",
    "thinking-block",
    "image-block",
    # agent
    "agent-item-idle",
    "agent-item-running",
    "agent-item-completed",
    # workflow
    "dag-progress-bar",
    "dag-progress-bar-complete",
    "dag-toolbar",
    "dag-toolbar-completed",
    "dag-node-pending",
    "dag-node-running",
    "dag-node-completed",
    "dag-node-failed",
]


class TestCatalogStories:
    """每个 story 独立渲染 + 截图。"""

    @pytest.mark.parametrize("story_name", STORY_NAMES)
    def test_story_baseline(
        self, context: BrowserContext, app_url: str, story_name: str
    ):
        page = context.new_page()
        try:
            page.goto(f"{app_url}/catalog/{story_name}")
            # 等 stage 出现 + 内容渲染
            page.wait_for_selector('[data-testid="catalog-stage"]', timeout=10_000)
            # 给 React 一点时间渲染图表 / markdown 等
            page.wait_for_timeout(400)

            actual = _screenshot_stage(page, f"catalog_{story_name}")
            _assert_or_update(actual, f"catalog_{story_name}", max_diff_ratio=0.01)
        finally:
            page.close()

    def test_story_not_found(self, context: BrowserContext, app_url: str):
        """不存在的 story → 显示 'not found' 页面。"""
        page = context.new_page()
        try:
            page.goto(f"{app_url}/catalog/__nonexistent__")
            page.wait_for_timeout(500)
            body = page.inner_text("body")
            assert "Story not found" in body
            assert "__nonexistent__" in body
        finally:
            page.close()
