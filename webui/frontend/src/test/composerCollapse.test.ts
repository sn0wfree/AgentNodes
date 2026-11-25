// 状态栏折叠档位纯函数（阶段 1 · 设计定稿 2026-11-25）
// 阈值来自 mockup 定稿：>520 全展开；≤520 短名；≤360 仅图标。
// 折叠依据卡片**实测宽度**（对齐 ZCode 测量哲学），本函数只负责"宽度 → 档位"。

import { describe, it, expect } from 'vitest'
import { collapseModeForWidth, COMPOSER_COLLAPSE_THRESHOLDS } from '../utils/composerCollapse'

describe('collapseModeForWidth', () => {
  it('full above the short threshold', () => {
    expect(collapseModeForWidth(630)).toBe('full')
    expect(collapseModeForWidth(521)).toBe('full')
  })

  it('short at exactly the short threshold and below', () => {
    expect(collapseModeForWidth(520)).toBe('short')
    expect(collapseModeForWidth(400)).toBe('short')
  })

  it('icon at exactly the icon threshold and below', () => {
    expect(collapseModeForWidth(360)).toBe('icon')
    expect(collapseModeForWidth(240)).toBe('icon')
  })

  it('handles non-finite and non-positive widths defensively', () => {
    expect(collapseModeForWidth(0)).toBe('icon')
    expect(collapseModeForWidth(-1)).toBe('icon')
    expect(collapseModeForWidth(NaN)).toBe('icon')
  })

  it('exposes thresholds for the legend/UI copy', () => {
    expect(COMPOSER_COLLAPSE_THRESHOLDS.short).toBe(520)
    expect(COMPOSER_COLLAPSE_THRESHOLDS.icon).toBe(360)
  })
})
