"""Visual regression diff utility.

对比两个 PNG 图片，返回：
- diff_pixel_count: 不同像素数
- diff_ratio: 差异比例 (0.0 = 完全一致, 1.0 = 完全不一致)
- diff_image: 差异可视化（红色叠加）

阈值说明：
- ``threshold`` (0-255) — 像素 RGB 任一通道差异超过此值算"不同"
- ``max_diff_ratio`` — 整体差异比例上限

支持 PIL 不可用时降级到 file size 比较。
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import NamedTuple

try:
    from PIL import Image, ImageChops
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class PixelDiffResult(NamedTuple):
    """视觉回归对比结果。"""

    match: bool                    # 是否通过
    diff_pixel_count: int          # 不同像素数（-1 = PIL 不可用）
    total_pixels: int              # 总像素数
    diff_ratio: float              # 差异比例
    diff_image_path: Path | None   # 差异可视化 PNG 路径


def compare_images(
    baseline: Path,
    actual: Path,
    *,
    threshold: int = 10,
    max_diff_ratio: float = 0.005,
    diff_output: Path | None = None,
) -> PixelDiffResult:
    """对比 baseline.png 与 actual.png。

    Args:
        baseline: 基线图片路径
        actual: 实际截图路径
        threshold: 像素差异阈值（0-255），任一通道差异 > threshold 算"不同"
        max_diff_ratio: 通过阈值（默认 0.5%）
        diff_output: 差异图保存路径（可选）

    Returns:
        PixelDiffResult
    """
    if not baseline.exists():
        raise FileNotFoundError(f"Baseline not found: {baseline}")
    if not actual.exists():
        raise FileNotFoundError(f"Actual not found: {actual}")

    if not HAS_PIL:
        # Fallback: file size comparison (very weak but works).
        # 降级不是静默的（2026-11-14）：文件大小相等 ≠ 视觉一致，这个绿灯
        # 假阳性曾经掩盖「视觉回归实际在空转」。必须让运行者看到告警。
        warnings.warn(
            "visual_diff: Pillow 未安装——降级为文件大小比较 (diff_pixel_count=-1)，"
            "视觉回归实质空转。请安装 Pillow（pip install '.[webui-api]'）。"
            f"baseline={baseline}",
            stacklevel=2,
        )
        match = baseline.stat().st_size == actual.stat().st_size
        return PixelDiffResult(
            match=match, diff_pixel_count=-1, total_pixels=0, diff_ratio=0.0,
            diff_image_path=None,
        )

    base_img = Image.open(baseline).convert("RGB")
    actual_img = Image.open(actual).convert("RGB")

    # 尺寸不一致 → 直接失败
    if base_img.size != actual_img.size:
        return PixelDiffResult(
            match=False,
            diff_pixel_count=base_img.size[0] * base_img.size[1],
            total_pixels=base_img.size[0] * base_img.size[1],
            diff_ratio=1.0,
            diff_image_path=None,
        )

    # 像素级 diff
    diff_img = ImageChops.difference(base_img, actual_img)
    base_img.load()
    actual_img.load()
    diff_pixels = diff_img.load()

    diff_count = 0
    total = base_img.size[0] * base_img.size[1]
    for y in range(base_img.size[1]):
        for x in range(base_img.size[0]):
            r, g, b = diff_pixels[x, y]
            if max(r, g, b) > threshold:
                diff_count += 1

    ratio = diff_count / total if total > 0 else 0.0
    match = ratio <= max_diff_ratio

    # 保存 diff 可视化（红色叠加）
    diff_path: Path | None = None
    if diff_output and not match:
        # Highlight diff pixels in red
        overlay = Image.new("RGB", base_img.size, (255, 0, 0))
        mask = Image.new("L", base_img.size, 0)
        mask_pixels = mask.load()
        for y in range(base_img.size[1]):
            for x in range(base_img.size[0]):
                r, g, b = diff_pixels[x, y]
                if max(r, g, b) > threshold:
                    mask_pixels[x, y] = 255
        diff_visual = Image.composite(overlay, actual_img, mask)
        diff_visual.save(diff_output)
        diff_path = diff_output

    return PixelDiffResult(
        match=match,
        diff_pixel_count=diff_count,
        total_pixels=total,
        diff_ratio=ratio,
        diff_image_path=diff_path,
    )


def save_baseline(actual: Path, baseline: Path) -> None:
    """保存 actual 为 baseline（用于 --update-snapshots）。"""
    baseline.parent.mkdir(parents=True, exist_ok=True)
    actual.replace(baseline)
