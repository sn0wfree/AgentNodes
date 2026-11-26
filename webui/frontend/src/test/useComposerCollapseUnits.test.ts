// useComposerCollapse · 补充单元测试（§9 批 9）
//
// 原测试只覆盖「不可测量 → full」。这里补齐 hook 的接线逻辑：
// - 宽度 → 档位映射（full / short / icon）
// - resize 事件触发重测
// - rAF / ResizeObserver 缺失时的降级分支
// - ref 未挂载时不注册监听
//
// 测法说明：jsdom 无布局，offsetParent 恒为 null → hook 回退到元素自身宽度
// （useComposerCollapse.ts:25）。故 mock HTMLElement.prototype.getBoundingClientRect。
// 另外 useLayoutEffect 依赖 [] 只跑一次 → 必须在**挂载前**让 ref 拿到真实元素，
// 所以用 render（真实 DOM）而非 renderHook。

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, act, renderHook } from '@testing-library/react'
import { createElement } from 'react'
import { useComposerCollapse } from '../hooks/useComposerCollapse'

let cardWidth = 700

function Probe({ onMode }: { onMode: (m: string) => void }) {
  const { ref, mode } = useComposerCollapse<HTMLDivElement>()
  onMode(mode)
  return createElement('div', { ref, 'data-mode': mode })
}

describe('useComposerCollapse — 宽度 → 档位', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    cardWidth = 700
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
      () => ({ width: cardWidth, height: 40 } as DOMRect),
    )
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('maps wide element to full', async () => {
    const modes: string[] = []
    render(createElement(Probe, { onMode: (m) => modes.push(m) }))
    await act(async () => {
      vi.advanceTimersByTime(200)
    })
    expect(modes[modes.length - 1]).toBe('full')
  })

  it('maps narrow element to icon', async () => {
    cardWidth = 300
    const modes: string[] = []
    render(createElement(Probe, { onMode: (m) => modes.push(m) }))
    await act(async () => {
      vi.advanceTimersByTime(200)
    })
    expect(modes[modes.length - 1]).toBe('icon')
  })

  it('maps mid-width element to short', async () => {
    cardWidth = 450
    const modes: string[] = []
    render(createElement(Probe, { onMode: (m) => modes.push(m) }))
    await act(async () => {
      vi.advanceTimersByTime(200)
    })
    expect(modes[modes.length - 1]).toBe('short')
  })

  it('re-measures on window resize', async () => {
    const modes: string[] = []
    render(createElement(Probe, { onMode: (m) => modes.push(m) }))
    await act(async () => {
      vi.advanceTimersByTime(200)
    })
    expect(modes[modes.length - 1]).toBe('full')

    cardWidth = 300
    await act(async () => {
      window.dispatchEvent(new Event('resize'))
      vi.advanceTimersByTime(300)
    })
    expect(modes[modes.length - 1]).toBe('icon')
  })

  it('falls back to direct update when requestAnimationFrame is unavailable', async () => {
    const original = globalThis.requestAnimationFrame
    // @ts-expect-error 故意删除以走降级分支
    delete globalThis.requestAnimationFrame
    try {
      const modes: string[] = []
      render(createElement(Probe, { onMode: (m) => modes.push(m) }))
      await act(async () => {
        vi.advanceTimersByTime(200)
      })
      expect(modes[modes.length - 1]).toBe('full')
    } finally {
      globalThis.requestAnimationFrame = original
    }
  })

  it('does not throw when ResizeObserver is unavailable', () => {
    const original = globalThis.ResizeObserver
    // @ts-expect-error 故意删除以走降级分支
    delete globalThis.ResizeObserver
    try {
      expect(() => render(createElement(Probe, { onMode: () => {} }))).not.toThrow()
    } finally {
      globalThis.ResizeObserver = original
    }
  })

  it('keeps full when width is unmeasurable (zero width)', async () => {
    cardWidth = 0
    const modes: string[] = []
    render(createElement(Probe, { onMode: (m) => modes.push(m) }))
    await act(async () => {
      vi.advanceTimersByTime(200)
    })
    expect(modes[modes.length - 1]).toBe('full')
  })
})

describe('useComposerCollapse — ref 未挂载', () => {
  it('does not register listeners when no element is attached', () => {
    // renderHook 不产生 DOM → ref.current 为 null → 提前 return（不挂监听）
    const addSpy = vi.spyOn(window, 'addEventListener')
    const { unmount } = renderHook(() => useComposerCollapse<HTMLDivElement>())
    unmount()
    expect(addSpy).not.toHaveBeenCalledWith('resize', expect.any(Function))
  })
})
