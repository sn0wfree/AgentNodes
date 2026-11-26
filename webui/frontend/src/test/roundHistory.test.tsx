// RoundHistory unit tests — verdict badges, metric rendering,
// expandable factor-failure details, empty state, and the
// per-round "open run" link added during the route-gap fix.

import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { RoundHistory } from '../components/study/RoundHistory'
import type { StudyRoundSummary } from '../api/client'

let warningIconCount = 0
vi.mock('lucide-react', () => {
  const Stub = () => null
  const AlertTriangle = () => {
    warningIconCount += 1
    return null
  }
  return { ChevronRight: Stub, ChevronDown: Stub, AlertTriangle, ExternalLink: Stub, FileText: Stub, X: Stub, GitCompare: Stub, RotateCcw: Stub, Loader2: Stub, Check: Stub }
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

describe('RoundHistory', () => {
  it('renders an empty state when there are no rounds', () => {
    render(<RoundHistory rounds={[]} currentRound={0} />)
    expect(screen.getByText('Round 历史')).toBeInTheDocument()
    expect(screen.getByText('暂无历史记录')).toBeInTheDocument()
  })

  it('shows run name, verdict badge and metric values', () => {
    render(<RoundHistory rounds={[round()]} currentRound={1} />)
    expect(screen.getByText('run_0001')).toBeInTheDocument()
    expect(screen.getByText(/keep/)).toBeInTheDocument()
    expect(screen.getByText('1.50')).toBeInTheDocument() // sharpe
    expect(screen.getByText('0.90')).toBeInTheDocument() // calmar
    expect(screen.getByText('-0.10')).toBeInTheDocument() // max_dd
  })

  it('renders dashes for missing metrics', () => {
    render(
      <RoundHistory
        rounds={[round({ metrics: undefined })]}
        currentRound={1}
      />
    )
    expect(screen.getAllByText('—').length).toBe(3)
  })

  it('highlights the current round row', () => {
    const { container } = render(
      <RoundHistory rounds={[round(), round({ round_num: 2, run_name: 'run_0002' })]} currentRound={2} />
    )
    // 配色统一阶段 2：bg-slate-800/50 → bg-surface（语义类）
    const currentRow = container.querySelector('.bg-surface')
    expect(currentRow).toBeInTheDocument()
  })

  it('expands a round to show factor failures with suggestions', () => {
    render(
      <RoundHistory
        rounds={[
          round({
            factor_failures: [
              {
                factor_code: 'F001',
                factor_name: 'F001',
                error: '数据不足',
                available_columns: ['close', 'volume'],
                suggested_fix: '增加采样窗口',
              },
            ],
          }),
        ]}
        currentRound={1}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: /run_0001/ }))
    expect(screen.getByText('F001')).toBeInTheDocument()
    expect(screen.getByText('数据不足')).toBeInTheDocument()
    expect(screen.getByText(/close, volume/)).toBeInTheDocument()
    expect(screen.getByText(/增加采样窗口/)).toBeInTheDocument()
  })

  it('renders a warning icon only when factor failures exist', () => {
    warningIconCount = 0
    render(
      <RoundHistory
        rounds={[
          round({ round_num: 1, factor_failures: [{ factor_code: 'F1', factor_name: 'F1', error: 'e' }] }),
          round({ round_num: 2, run_name: 'run_0002' }),
        ]}
        currentRound={1}
      />
    )
    // One AlertTriangle renders for the failed round row only.
    expect(warningIconCount).toBe(1)
  })

  it('calls onOpenRun with the run name when the link is clicked', () => {
    const onOpenRun = vi.fn()
    render(
      <RoundHistory
        rounds={[round(), round({ round_num: 2, run_name: 'run_0002' })]}
        currentRound={1}
        onOpenRun={onOpenRun}
      />
    )
    const buttons = screen.getAllByTitle('查看回测产物')
    expect(buttons).toHaveLength(2)
    fireEvent.click(buttons[1])
    expect(onOpenRun).toHaveBeenCalledWith('run_0002')
  })

  it('renders verdict-colored timeline dots and connectors', () => {
    const { container } = render(
      <RoundHistory
        rounds={[
          round({ round_num: 1, verdict: 'keep' }),
          round({ round_num: 2, verdict: 'review' }),
          round({ round_num: 3, verdict: 'discard' }),
        ]}
        currentRound={3}
      />
    )
    // keep → emerald dot, review → amber dot, discard → slate dot (+ badges)
    expect(container.querySelectorAll('.border-emerald-500').length).toBe(1)
    expect(container.querySelectorAll('[class*="border-warning"]').length).toBe(2) // review badge + dot
    // discard → slate dot：配色统一阶段 2 后为 border-strong（语义类）
    expect(container.querySelectorAll('.border-strong').length).toBe(2)
    // connectors inherit the verdict color of the round
    expect(container.querySelectorAll('[class*="bg-emerald-500/40"]').length).toBe(1)
    expect(container.querySelectorAll('[class*="bg-warning/40"]').length).toBe(1)
    // last round has no connector below it
    expect(container.querySelectorAll('.flex-1.w-0\\.5').length).toBe(2)
  })

  it('pulses the timeline dot of the current round', () => {
    const { container } = render(
      <RoundHistory
        rounds={[round(), round({ round_num: 2 })]}
        currentRound={2}
      />
    )
    expect(container.querySelectorAll('.animate-pulse').length).toBe(1)
  })

  it('does not render open-run buttons when onOpenRun is absent', () => {
    render(<RoundHistory rounds={[round()]} currentRound={1} />)
    expect(screen.queryAllByTitle('查看回测产物')).toHaveLength(0)
  })

  it('opens the detail drawer when studyId is provided and a round is clicked', () => {
    // The drawer fires API calls on mount; stub them to resolve nothing.
    vi.mock('../api/client', () => ({ api: { study: {} } }))
    render(<RoundHistory rounds={[round()]} currentRound={1} studyId="st-1" />)
    fireEvent.click(screen.getByText('R1'))
    // Drawer header appears once the row is opened.
    expect(screen.getByText('R1 详情')).toBeTruthy()
  })
})
