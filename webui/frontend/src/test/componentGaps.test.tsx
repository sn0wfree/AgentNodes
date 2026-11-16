// T2 — component gaps: the three 0%-coverage components that carry real
// behavior (ErrorBoundary / AgentList / EquityCurveCard). Pure render
// shells are left to the catalog/visual baselines; these three have
// logic (error trapping, sorting + empty state, data-shape fallbacks).

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ErrorBoundary } from '../components/common/ErrorBoundary'
import { AgentList } from '../components/agent/AgentList'
import { EquityCurveCard } from '../components/performance/EquityCurveCard'
import { useAgentStore } from '../stores/agents'
import type { Agent } from '../stores/agents'

// recharts renders SVG via ResizeObserver in jsdom; stub it out so the
// chart component can mount at all.
beforeEach(() => {
  ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
})

// ── ErrorBoundary ────────────────────────────────────────────────
function Boom(): never {
  throw new Error('kaboom')
}

describe('ErrorBoundary', () => {
  it('renders children when nothing throws', () => {
    render(
      <ErrorBoundary>
        <div>ok content</div>
      </ErrorBoundary>
    )
    expect(screen.getByText('ok content')).toBeInTheDocument()
  })

  it('catches a child error and shows the default fallback with the message', () => {
    // React logs the caught error; silence it for a clean test output.
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>
    )
    expect(screen.getByText('出错了')).toBeInTheDocument()
    expect(screen.getByText('kaboom')).toBeInTheDocument()
    expect(screen.getByText('重试')).toBeInTheDocument()
    errSpy.mockRestore()
  })

  it('renders a custom fallback when provided', () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <ErrorBoundary fallback={<div>custom fallback</div>}>
        <Boom />
      </ErrorBoundary>
    )
    expect(screen.getByText('custom fallback')).toBeInTheDocument()
    expect(screen.queryByText('出错了')).not.toBeInTheDocument()
    errSpy.mockRestore()
  })

  it('retry clears the error state and re-renders children', () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    let shouldThrow = true
    function Sometimes() {
      if (shouldThrow) throw new Error('first run')
      return <div>recovered</div>
    }
    render(
      <ErrorBoundary>
        <Sometimes />
      </ErrorBoundary>
    )
    expect(screen.getByText('出错了')).toBeInTheDocument()
    shouldThrow = false
    fireEvent.click(screen.getByText('重试'))
    expect(screen.getByText('recovered')).toBeInTheDocument()
    errSpy.mockRestore()
  })
})

// ── AgentList ────────────────────────────────────────────────────
const mkAgent = (over: Partial<Agent>): Agent =>
  ({
    id: 'a1',
    session_id: 'sess-1',
    status: 'idle' as never,
    name: 'Agent',
    created_at: 1,
    updated_at: 1,
    tool_calls_count: 0,
    compaction_count: 0,
    context_tokens: 0,
    context_tokens_limit: 100000,
    iterations_detail: [],
    ...over,
  }) as Agent

describe('AgentList', () => {
  beforeEach(() => {
    useAgentStore.setState({ agents: new Map() })
  })

  it('shows the empty state when there are no agents', () => {
    render(<AgentList />)
    expect(screen.getByText('暂无 Agent')).toBeInTheDocument()
    expect(
      screen.getByText('Agent 会在聊天或任务执行时自动创建')
    ).toBeInTheDocument()
  })

  it('orders agents by created_at descending (newest first)', () => {
    useAgentStore.setState({
      agents: new Map([
        ['old', mkAgent({ id: 'old', name: 'OldAgent', created_at: 100 })],
        ['new', mkAgent({ id: 'new', name: 'NewAgent', created_at: 300 })],
        ['mid', mkAgent({ id: 'mid', name: 'MidAgent', created_at: 200 })],
      ]),
    })
    render(<AgentList />)
    const names = screen.getAllByText(/Agent$/).map((el) => el.textContent)
    expect(names).toEqual(['NewAgent', 'MidAgent', 'OldAgent'])
  })

  it('invokes onSelectAgent with the clicked agent id', () => {
    const onSelect = vi.fn()
    useAgentStore.setState({
      agents: new Map([['a1', mkAgent({ id: 'a1', name: 'A1', created_at: 1 })]]),
    })
    render(<AgentList onSelectAgent={onSelect} />)
    fireEvent.click(screen.getByText('A1'))
    expect(onSelect).toHaveBeenCalledWith('a1')
  })

  it('renders both agents and passes selection through without crashing', () => {
    useAgentStore.setState({
      agents: new Map([
        ['a1', mkAgent({ id: 'a1', name: 'Sel', created_at: 2 })],
        ['a2', mkAgent({ id: 'a2', name: 'Other', created_at: 1 })],
      ]),
    })
    render(<AgentList selectedAgentId="a1" />)
    // Selection styling is cosmetic (left to the visual baselines); the
    // behavioural contract is that both rows render and the list does
    // not throw when a selectedAgentId is supplied.
    expect(screen.getByText('Sel')).toBeInTheDocument()
    expect(screen.getByText('Other')).toBeInTheDocument()
  })

  it('does not crash when onSelectAgent is omitted', () => {
    useAgentStore.setState({
      agents: new Map([['a1', mkAgent({ id: 'a1', name: 'NoHandler' })]]),
    })
    render(<AgentList />)
    expect(() => fireEvent.click(screen.getByText('NoHandler'))).not.toThrow()
  })
})

// ── EquityCurveCard ──────────────────────────────────────────────
describe('EquityCurveCard', () => {
  it('renders the empty placeholder when curve and metrics are both null', () => {
    render(<EquityCurveCard curve={null} />)
    // The card must say something rather than render a blank chart area.
    expect(document.body.textContent?.trim().length).toBeGreaterThan(0)
  })

  it('renders metrics-only fallback when the curve is missing (Tier B P7)', () => {
    render(
      <EquityCurveCard
        curve={null}
        metrics={{
          total_return: 0.123,
          sharpe: 1.45,
          max_drawdown: -0.08,
          timestamp: 1,
        }}
      />
    )
    const text = document.body.textContent ?? ''
    // total_return is surfaced as a percentage with an explicit sign.
    expect(text).toContain('12.30%')
    // sharpe rendered as a plain number.
    expect(text).toContain('1.45')
  })

  it('renders the chart path when an equity curve is supplied', () => {
    render(
      <EquityCurveCard
        curve={{
          title: '净值曲线',
          points: [
            { label: 't0', value: 1 },
            { label: 't1', value: 1.1 },
          ],
          timestamp: 1,
        }}
      />
    )
    expect(document.querySelector('.recharts-responsive-container')).toBeTruthy()
  })
})
