// useComposerCollapse：按卡片实测宽度判定折叠档位（设计定稿 2026-11-25）

import { describe, it, expect } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useComposerCollapse } from '../hooks/useComposerCollapse'

describe('useComposerCollapse', () => {
  it('defaults to full when no element is measurable (jsdom/SSR)', () => {
    const { result } = renderHook(() => useComposerCollapse<HTMLDivElement>())
    expect(result.current.mode).toBe('full')
  })

  it('exposes a ref for the status bar', () => {
    const { result } = renderHook(() => useComposerCollapse<HTMLDivElement>())
    expect(result.current.ref).toBeDefined()
  })
})
