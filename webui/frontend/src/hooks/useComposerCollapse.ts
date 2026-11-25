/**
 * 状态栏折叠档位 hook（设计定稿 2026-11-25）。
 *
 * 按**卡片**实测宽度判定档位（>520 full / ≤520 short / ≤360 icon）：
 * - ResizeObserver 观察卡片（状态栏的 offsetParent），局部宽度变化即响应
 * - rAF + 短延迟兜底：首屏布局未完成 / RO 首帧旧宽度时补测
 * - window.resize 兜底：视口切换时同步重测
 * - 宽度不可得（jsdom/SSR）时保持 full——不误折叠
 */
import { useLayoutEffect, useRef, useState } from 'react'
import {
  collapseModeForWidth,
  type ComposerCollapseMode,
} from '../utils/composerCollapse'

export function useComposerCollapse<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [mode, setMode] = useState<ComposerCollapseMode>('full')

  useLayoutEffect(() => {
    const update = () => {
      const bar = ref.current
      if (!bar) return
      const card = bar.offsetParent as HTMLElement | null
      const w = card?.getBoundingClientRect().width ?? bar.getBoundingClientRect().width
      if (w > 0) setMode(collapseModeForWidth(w))
    }
    if (!ref.current) return
    update()
    const raf = typeof requestAnimationFrame === 'function' ? requestAnimationFrame(update) : 0
    const timer = setTimeout(update, 120)
    const schedule = () => {
      if (typeof requestAnimationFrame === 'function') requestAnimationFrame(update)
      else update()
    }
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedule)
    if (ro) ro.observe((ref.current.offsetParent ?? ref.current) as Element)
    window.addEventListener('resize', schedule)
    return () => {
      ro?.disconnect()
      window.removeEventListener('resize', schedule)
      if (raf) cancelAnimationFrame(raf)
      clearTimeout(timer)
    }
  }, [])

  return { ref, mode }
}
