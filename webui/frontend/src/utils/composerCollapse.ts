/**
 * 状态栏折叠档位（阶段 1 · 设计定稿 2026-11-25）。
 *
 * 阈值来自 mockup 定稿（composer-stage1-mockup.html 折叠说明）：
 * - `> 520px` 全展开（persona/模式带文字、模型全名、思考可见）
 * - `≤ 520px` 短名：隐藏思考、模型改短名
 * - `≤ 360px` 仅图标：persona/模式/模型只留图标；圆环与发送始终保留
 *
 * 判定依据是卡片**实测宽度**（ResizeObserver / getBoundingClientRect），
 * 不是视口断点——滑块/侧栏都会改变卡片真实可用宽（ZCode 测量哲学的简化版）。
 * 纯函数，便于单测；组件层负责测量并调用本函数。
 */

export type ComposerCollapseMode = 'full' | 'short' | 'icon'

export const COMPOSER_COLLAPSE_THRESHOLDS = {
  /** ≤ 该宽度：短名档 */
  short: 520,
  /** ≤ 该宽度：仅图标档 */
  icon: 360,
} as const

export function collapseModeForWidth(width: number): ComposerCollapseMode {
  if (!Number.isFinite(width) || width <= 0) return 'icon'
  if (width <= COMPOSER_COLLAPSE_THRESHOLDS.icon) return 'icon'
  if (width <= COMPOSER_COLLAPSE_THRESHOLDS.short) return 'short'
  return 'full'
}
