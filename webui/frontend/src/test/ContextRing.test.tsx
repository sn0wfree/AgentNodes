// R-014 H2 —— ContextRing 分段圆环组件。
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { buildArcs, ContextRing, BreakdownList, RING_SEGMENT_META } from '../components/chat/ContextRing'

describe('buildArcs', () => {
  it('returns empty when there is no segment data', () => {
    expect(buildArcs({}, 100)).toEqual([])
  })

  it('one arc per non-zero segment, ratios sum to 1', () => {
    const arcs = buildArcs({ messages: 50, skills: 30, mcp_tool_schemas: 20 }, 100)
    expect(arcs.length).toBe(3)
    expect(arcs.reduce((s, a) => s + a.ratio, 0)).toBeCloseTo(1)
    // dash 长度与占比一致
    expect(arcs[0].dashArray.startsWith('50 ')).toBe(true)
  })

  it('skips zero-value segments', () => {
    const arcs = buildArcs({ messages: 10, skills: 0 }, 100)
    expect(arcs.length).toBe(1)
    expect(arcs[0].key).toBe('messages')
  })
})

describe('ContextRing render', () => {
  const segs = {
    messages: 50, system_prompt: 20, meta_user_context: 10,
    skills: 5, tool_prompt: 10, system_tool_schemas: 5, mcp_tool_schemas: 0,
  }

  it('renders one circle per non-zero segment', () => {
    const { container } = render(<ContextRing segments={segs} percent={42} tone="normal" />)
    expect(screen.getByTestId('context-ring')).toBeTruthy()
    expect(container.querySelectorAll('[data-testid^="context-ring-seg-"]').length).toBe(6)
    expect(container.querySelector('[data-testid="context-ring-single"]')).toBeNull()
  })

  it('falls back to a single tone-colored arc without segment data', () => {
    const { container } = render(<ContextRing segments={{}} percent={75} tone="danger" />)
    const single = container.querySelector('[data-testid="context-ring-single"]') as SVGCircleElement
    expect(single).toBeTruthy()
    expect(single.getAttribute('stroke')).toContain('danger')
  })

  it('supports a larger size for the right panel', () => {
    render(<ContextRing segments={segs} percent={42} tone="normal" size={64} />)
    const ring = screen.getByTestId('context-ring')
    expect(ring.getAttribute('width')).toBe('64')
  })
})

describe('BreakdownList', () => {
  it('renders all 7 rows with labels and percentages', () => {
    const segs = Object.fromEntries(RING_SEGMENT_META.map((m) => [m.key, 10]))
    render(<BreakdownList segments={segs} />)
    for (const m of RING_SEGMENT_META) {
      expect(screen.getByTestId(`breakdown-row-${m.key}`).textContent).toContain(m.label)
    }
  })

  it('shows a placeholder without data', () => {
    render(<BreakdownList segments={{}} />)
    expect(screen.getByText(/分段数据待接入/)).toBeTruthy()
  })
})
