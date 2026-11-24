// MetricsCompare — W&B-style per-round comparison table: best value per
// column highlighted, verdict chips, optional run links.

import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MetricsCompare } from '../components/study/MetricsCompare'
import type { StudyRoundSummary } from '../api/client'

vi.mock('lucide-react', () => {
  const Stub = () => null
  return { BarChart3: Stub, ExternalLink: Stub }
})

function round(overrides: Partial<StudyRoundSummary> = {}): StudyRoundSummary {
  return {
    round_num: 1,
    run_name: 'run_0001',
    metrics: { sharpe: 1.5, calmar: 0.9, max_dd: -0.1 },
    verdict: 'keep',
    created_at: '2026-08-01T10:30:00',
    ...overrides,
  }
}

describe('MetricsCompare', () => {
  it('shows the empty state without metrics', () => {
    render(<MetricsCompare rounds={[round({ metrics: null })]} />)
    expect(screen.getByText('指标对比')).toBeInTheDocument()
    expect(screen.getByText('暂无带指标的轮次')).toBeInTheDocument()
  })

  it('renders one row per round sorted by round number', () => {
    render(<MetricsCompare rounds={[round({ round_num: 2 }), round({ round_num: 1 })]} />)
    const rows = screen.getAllByRole('row')
    // header + 2 rows
    expect(rows).toHaveLength(3)
    expect(rows[1]).toHaveTextContent('R1')
    expect(rows[2]).toHaveTextContent('R2')
    expect(screen.getAllByText('1.50').length).toBeGreaterThan(0)
  })

  it('highlights the best value in each column', () => {
    const { container } = render(
      <MetricsCompare
        rounds={[
          round({ round_num: 1, metrics: { sharpe: 1.5, calmar: 0.9, max_dd: -0.1 } }),
          round({ round_num: 2, metrics: { sharpe: 2.1, calmar: 0.4, max_dd: -0.3 } }),
        ]}
      />
    )
    const bestCells = container.querySelectorAll('td.text-success-fg')
    expect(bestCells.length).toBe(3) // sharpe R2, calmar R1, max_dd R1
  })

  it('renders verdict chips', () => {
    render(
      <MetricsCompare
        rounds={[
          round({ verdict: 'keep' }),
          round({ round_num: 2, verdict: 'review' }),
          round({ round_num: 3, verdict: 'discard' }),
        ]}
      />
    )
    expect(screen.getByText('keep')).toBeInTheDocument()
    expect(screen.getByText('review')).toBeInTheDocument()
    expect(screen.getByText('discard')).toBeInTheDocument()
  })

  it('calls onOpenRun with the run name when the link is clicked', () => {
    const onOpenRun = vi.fn()
    render(
      <MetricsCompare
        rounds={[round(), round({ round_num: 2, run_name: 'run_0002' })]}
        onOpenRun={onOpenRun}
      />
    )
    const buttons = screen.getAllByTitle('查看回测产物')
    expect(buttons).toHaveLength(2)
    fireEvent.click(buttons[1])
    expect(onOpenRun).toHaveBeenCalledWith('run_0002')
  })

  it('omits run links when onOpenRun is absent', () => {
    render(<MetricsCompare rounds={[round()]} />)
    expect(screen.queryAllByTitle('查看回测产物')).toHaveLength(0)
  })
})
