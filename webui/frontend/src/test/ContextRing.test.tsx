// ContextRing 分段圆环组件（§9 复核批：后端细拆 10 段 → 前端配置聚合）。
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  buildArcs, ContextRing, BreakdownList, breakdownGroups, aggregateBreakdown,
} from '../components/chat/ContextRing'

const GROUPS = breakdownGroups()

describe('aggregateBreakdown（配置驱动聚合）', () => {
  it('sums fine-grained backend segments into display rows', () => {
    const fine = {
      system_prompt: 100, user_text: 20, assistant_text: 16,
      tool_calls: 30, tool_results: 90, meta_user_context: 0,
      skills: 5, tool_prompt: 40, system_tool_schemas: 60, mcp_tool_schemas: 0,
    }
    const agg = aggregateBreakdown(fine, GROUPS)
    expect(agg.messages).toBe(36)        // user_text + assistant_text
    expect(agg.tool_traffic).toBe(120)   // tool_calls + tool_results
    expect(agg.system_prompt).toBe(100)
    expect(agg.skills).toBe(5)
  })

  it('yields 0 for groups whose sources are all absent', () => {
    const agg = aggregateBreakdown({}, GROUPS)
    expect(Object.values(agg).every((v) => v === 0)).toBe(true)
  })

  it('routes unconfigured backend segments to 其他 (§9: 不配则显示其他类别)', () => {
    // 后端新增细段 reasoning（前端未配置）→ 归入「其他」
    const fine = {
      system_prompt: 100, user_text: 20, assistant_text: 16,
      tool_calls: 30, tool_results: 90, tool_prompt: 40,
      system_tool_schemas: 60, reasoning: 3000,
    }
    const agg = aggregateBreakdown(fine, GROUPS)
    expect(agg.other).toBe(3000)
  })

  it('keeps displayed total equal to backend total when a segment is unconfigured', () => {
    // 核心契约：未配置的段不能消失，否则占比失真
    const fine = {
      system_prompt: 100, user_text: 20, assistant_text: 16,
      tool_calls: 30, tool_results: 90, tool_prompt: 40,
      system_tool_schemas: 60, reasoning: 3000,
    }
    const agg = aggregateBreakdown(fine, GROUPS)
    const displayed = Object.values(agg).reduce((a, b) => a + b, 0)
    const backendTotal = Object.values(fine).reduce((a, b) => a + b, 0)
    expect(displayed).toBe(backendTotal)
  })

  it('其他 row is 0 when every backend segment is configured', () => {
    const fine = {
      system_prompt: 100, user_text: 20, assistant_text: 16,
      tool_calls: 30, tool_results: 90, meta_user_context: 5,
      skills: 5, tool_prompt: 40, system_tool_schemas: 60, mcp_tool_schemas: 7,
    }
    const agg = aggregateBreakdown(fine, GROUPS)
    expect(agg.other).toBe(0)
  })
})

describe('buildArcs', () => {
  it('returns empty when there is no segment data', () => {
    expect(buildArcs({}, 100)).toEqual([])
  })

  it('one arc per non-zero display row, ratios sum to 1', () => {
    const arcs = buildArcs({ messages: 50, skills: 30, mcp_tool_schemas: 20 }, 100)
    expect(arcs.length).toBe(3)
    expect(arcs.reduce((s, a) => s + a.ratio, 0)).toBeCloseTo(1)
    // dash 长度与占比一致
    expect(arcs[0].dashArray.startsWith('50 ')).toBe(true)
  })

  it('skips zero-value rows', () => {
    const arcs = buildArcs({ messages: 10, skills: 0 }, 100)
    expect(arcs.length).toBe(1)
    expect(arcs[0].key).toBe('messages')
  })
})

describe('ContextRing render', () => {
  // 聚合后的展示行数据（与配置 groups 的 key 对应）
  const segs = aggregateBreakdown({
    system_prompt: 20, user_text: 25, assistant_text: 25,
    tool_calls: 0, tool_results: 10, meta_user_context: 10,
    skills: 5, tool_prompt: 10, system_tool_schemas: 5, mcp_tool_schemas: 0,
  }, GROUPS)

  it('renders one circle per non-zero display row', () => {
    const { container } = render(<ContextRing segments={segs} percent={42} tone="normal" />)
    expect(screen.getByTestId('context-ring')).toBeTruthy()
    // 8 行中 mcp_tool_schemas=0、tool_calls+tool_results 聚合 10 >0 → 7 行非零
    expect(container.querySelectorAll('[data-testid^="context-ring-seg-"]').length).toBe(7)
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
  it('renders all display rows from the aggregation config', () => {
    const segs = Object.fromEntries(GROUPS.map((m) => [m.key, 10]))
    render(<BreakdownList segments={segs} />)
    for (const m of GROUPS) {
      expect(screen.getByTestId(`breakdown-row-${m.key}`).textContent).toContain(m.label)
    }
  })

  it('aggregates fine-grained backend segments into display rows', () => {
    // 后端细拆：user_text 20 + assistant_text 16 → 「历史消息」36
    render(<BreakdownList segments={{ user_text: 20, assistant_text: 16 }} />)
    expect(screen.getByTestId('breakdown-row-messages').textContent).toContain('36')
  })

  it('shows 工具流量 as its own row (§9: split out of 历史消息)', () => {
    render(<BreakdownList segments={{ tool_calls: 30, tool_results: 90 }} />)
    const row = screen.getByTestId('breakdown-row-tool_traffic')
    expect(row.textContent).toContain('工具流量')
    expect(row.textContent).toContain('120')
  })

  it('shows 其他 row for backend segments the config does not cover', () => {
    render(<BreakdownList segments={{ reasoning: 3000, user_text: 20 }} />)
    const other = screen.getByTestId('breakdown-row-other')
    expect(other.textContent).toContain('其他')
    expect(other.textContent).toContain('3,000')
  })

  it('hides the 其他 row when there is nothing unconfigured', () => {
    render(<BreakdownList segments={{ user_text: 20, system_prompt: 100 }} />)
    expect(screen.queryByTestId('breakdown-row-other')).toBeNull()
  })

  it('shows a placeholder without data', () => {
    render(<BreakdownList segments={{}} />)
    expect(screen.getByText(/分段数据待接入/)).toBeTruthy()
  })
})
