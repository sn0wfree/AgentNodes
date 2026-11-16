// T3.2 — StrategyLibraryPage integration. Focus on the parts that carry
// behaviour: the three-way Promise.allSettled degradation (one endpoint
// failing must not blank the page), metric formatting (ratios → %),
// study-status badges, and card/row navigation targets.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render as rtlRender, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const navigateMock = vi.hoisted(() => vi.fn())
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('react-router-dom')
  return { ...actual, useNavigate: () => navigateMock }
})

vi.mock('../api/client', () => ({
  api: {
    strategies: { list: vi.fn() },
    run: { list: vi.fn() },
    study: { list: vi.fn() },
  },
}))

import { api } from '../api/client'
import { useSystemStore } from '../stores/system'
import { useSessionStore } from '../stores/session'
import { StrategyLibraryPage } from '../pages/StrategyLibraryPage'

// PageShell mounts IconNav (router hooks), so every render needs a Router.
const render = (ui: import('react').ReactElement) =>
  rtlRender(<MemoryRouter>{ui}</MemoryRouter>)

const STRATEGY = (over: Record<string, unknown> = {}) => ({
  name: 'momentum',
  has_strategy_py: true,
  has_config_yaml: true,
  ...over,
})

beforeEach(() => {
  navigateMock.mockReset()
  useSystemStore.setState({ workspacePath: '/ws/demo' } as never)
  useSessionStore.setState({ currentSessionId: 'sess-1' } as never)
  vi.mocked(api.strategies.list).mockReset()
  vi.mocked(api.run.list).mockReset()
  vi.mocked(api.study.list).mockReset()
  vi.mocked(api.strategies.list).mockResolvedValue({ strategies: [] } as never)
  vi.mocked(api.run.list).mockResolvedValue({ runs: [] } as never)
  vi.mocked(api.study.list).mockResolvedValue({ studies: [] } as never)
})

describe('StrategyLibraryPage', () => {
  it('shows the empty state when the workspace has no strategies', async () => {
    render(<StrategyLibraryPage />)
    await waitFor(() => expect(screen.getByText('暂无策略')).toBeInTheDocument())
  })

  it('warns when no workspace path is loaded and skips the API calls', async () => {
    useSystemStore.setState({ workspacePath: '' } as never)
    render(<StrategyLibraryPage />)
    expect(
      screen.getByText('未加载到 workspace 路径，请先在设置中确认系统信息')
    ).toBeInTheDocument()
    // No fetch should fire without a workspace path.
    expect(api.strategies.list).not.toHaveBeenCalled()
  })

  it('renders a strategy card with file badges and run count', async () => {
    vi.mocked(api.strategies.list).mockResolvedValue({
      strategies: [
        STRATEGY({ name: 'momentum' }),
        STRATEGY({ name: 'bare', has_strategy_py: false, has_config_yaml: false }),
      ],
    } as never)
    render(<StrategyLibraryPage />)
    await waitFor(() => expect(screen.getByText('momentum')).toBeInTheDocument())

    expect(screen.getByText('bare')).toBeInTheDocument()
    expect(screen.getAllByText('strategy.py')).toHaveLength(1)
    expect(screen.getByText('无 strategy.py')).toBeInTheDocument()
    expect(screen.getByText('config.yaml')).toBeInTheDocument()
    expect(screen.getByText('无 config.yaml')).toBeInTheDocument()
    expect(screen.getAllByText('run ×0')).toHaveLength(2)
  })

  it('formats ratio metrics as percentages and missing metrics as an em dash', async () => {
    vi.mocked(api.strategies.list).mockResolvedValue({
      strategies: [STRATEGY({ name: 'momentum' })],
    } as never)
    vi.mocked(api.run.list).mockResolvedValue({
      runs: [{
        name: 'run_0003',
        metrics: { total_return: 0.1234, sharpe: 1.5, calmar: -0.02, max_drawdown: -0.08 },
      }],
    } as never)
    render(<StrategyLibraryPage />)
    await waitFor(() => expect(screen.getByText('12.3%')).toBeInTheDocument())

    // ratio keys are multiplied by 100 and fixed to one decimal
    expect(screen.getByText('150.0%')).toBeInTheDocument()   // sharpe
    expect(screen.getByText('-2.0%')).toBeInTheDocument()    // calmar
    expect(screen.getByText('-8.0%')).toBeInTheDocument()    // max_drawdown
    expect(screen.getByText('run_0003')).toBeInTheDocument()
  })

  it('renders missing metrics as — (dash fallback)', async () => {
    vi.mocked(api.strategies.list).mockResolvedValue({
      strategies: [STRATEGY({ name: 'no-run' })],
    } as never)
    render(<StrategyLibraryPage />)
    await waitFor(() => expect(screen.getByText('no-run')).toBeInTheDocument())
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(4)
  })

  it('survives a failing strategies endpoint (allSettled degradation)', async () => {
    vi.mocked(api.strategies.list).mockRejectedValueOnce(new Error('strategies down'))
    vi.mocked(api.run.list).mockResolvedValue({
      runs: [{ name: 'r1', metrics: { total_return: 0.05 } }],
    } as never)
    render(<StrategyLibraryPage />)
    // Page stays usable: no strategies, but no blank screen either.
    await waitFor(() => expect(screen.getByText('暂无策略')).toBeInTheDocument())
  })

  it('still renders strategies when the runs endpoint fails', async () => {
    vi.mocked(api.strategies.list).mockResolvedValue({
      strategies: [STRATEGY({ name: 'momentum' })],
    } as never)
    vi.mocked(api.run.list).mockRejectedValueOnce(new Error('runs down'))
    render(<StrategyLibraryPage />)
    await waitFor(() => expect(screen.getByText('momentum')).toBeInTheDocument())
    // With no runs, metrics fall back to dashes rather than crashing.
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(4)
  })

  it('renders study badges per strategy, capped at three', async () => {
    vi.mocked(api.strategies.list).mockResolvedValue({
      strategies: [STRATEGY({ name: 'momentum' })],
    } as never)
    vi.mocked(api.study.list).mockResolvedValue({
      studies: [1, 2, 3, 4].map((i) => ({
        study_id: `st-${i}`,
        strategy_name: 'momentum',
        execution_status: i === 1 ? 'running' : 'paused',
        current_round: i,
        objective: `obj ${i}`,
      })),
    } as never)
    render(<StrategyLibraryPage />)
    // The same studies also render into the history table (which shows
    // 轮次 as a column value, not the "轮次 N" badge form). Scope the
    // assertion to the badge wording so the cap is what's measured.
    await waitFor(() =>
      expect(screen.getAllByText(/· 轮次 \d+$/).length).toBe(3)
    )
    expect(screen.getByText(/研究中 · 轮次 1/)).toBeInTheDocument()
    expect(screen.getByText(/已暂停 · 轮次 2/)).toBeInTheDocument()
    // The 4th study is capped out of the badge row.
    expect(screen.queryByText(/· 轮次 4$/)).not.toBeInTheDocument()
  })

  it('renders the study history table with status labels + verdicts', async () => {
    vi.mocked(api.study.list).mockResolvedValue({
      studies: [
        {
          study_id: 'st-1', strategy_name: 'momentum', execution_status: 'running',
          current_round: 4, objective: '研究动量', last_verdict: 'keep',
        },
        {
          study_id: 'st-2', strategy_name: 'value', execution_status: 'failed',
          current_round: 2, objective: '', last_verdict: null,
        },
      ],
    } as never)
    render(<StrategyLibraryPage />)
    await waitFor(() => expect(screen.getByText('研究记录')).toBeInTheDocument())

    expect(screen.getByText('研究动量')).toBeInTheDocument()
    // Empty objective / verdict fall back to an em dash.
    expect(screen.getByText('keep')).toBeInTheDocument()
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2)
  })

  it('navigates to the run detail when a strategy card is clicked', async () => {
    vi.mocked(api.strategies.list).mockResolvedValue({
      strategies: [STRATEGY({ name: 'momentum' })],
    } as never)
    vi.mocked(api.run.list).mockResolvedValue({
      runs: [{ name: 'run_0007', metrics: {} }],
    } as never)
    render(<StrategyLibraryPage />)
    await waitFor(() => expect(screen.getByText('momentum')).toBeInTheDocument())

    fireEvent.click(screen.getByText('momentum'))
    expect(navigateMock).toHaveBeenCalledWith('/run/momentum/run_0007')
  })

  it('navigates to the study detail when a history row is clicked', async () => {
    vi.mocked(api.study.list).mockResolvedValue({
      studies: [{
        study_id: 'st-9', strategy_name: 'momentum', execution_status: 'completed',
        current_round: 5, objective: '目标 X', last_verdict: 'done',
      }],
    } as never)
    render(<StrategyLibraryPage />)
    await waitFor(() => expect(screen.getByText('目标 X')).toBeInTheDocument())

    fireEvent.click(screen.getByText('目标 X'))
    expect(navigateMock).toHaveBeenCalledWith('/study/st-9')
  })

  it('skips the study request when there is no current session', async () => {
    useSessionStore.setState({ currentSessionId: null } as never)
    render(<StrategyLibraryPage />)
    await waitFor(() => expect(screen.getByText('暂无策略')).toBeInTheDocument())
    expect(api.study.list).not.toHaveBeenCalled()
  })
})
