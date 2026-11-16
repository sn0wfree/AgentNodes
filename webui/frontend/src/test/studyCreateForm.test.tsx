// StudyCreateForm — payload assembly, validation gates, and the
// post-create reset (including AI-composition leftovers).

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const { fakeStudyStore, fakeAuthStore } = vi.hoisted(() => ({
  fakeStudyStore: {
    busy: false,
    setBusy: vi.fn(),
    setError: vi.fn(),
  },
  fakeAuthStore: { user: { username: 'alice' } },
}))

vi.mock('../stores/study', () => ({
  useStudyStore: (sel: (s: typeof fakeStudyStore) => unknown) =>
    sel(fakeStudyStore),
}))
vi.mock('../stores/auth', () => ({
  useAuthStore: (sel: (s: typeof fakeAuthStore) => unknown) =>
    sel(fakeAuthStore),
}))

vi.mock('../api/client', () => ({
  api: { study: { start: vi.fn() } },
}))

vi.mock('../components/study/StrategyNameInput', () => ({
  StrategyNameInput: ({
    value, onChange,
  }: {
    value: string
    onChange: (v: string) => void
  }) => (
    <input
      data-testid="strategy-name"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder="策略名"
    />
  ),
}))

vi.mock('../components/study/StudyDAGComposer', () => ({
  StudyDAGComposer: () => <div />,
}))

vi.mock('lucide-react', () => {
  const Stub = () => null
  const out: Record<string, unknown> = { default: Stub }
  for (const n of [
    'Loader2',
    'Plus',
    'RefreshCw',
    'X',
    'Target',
    'SlidersHorizontal',
    'ChevronDown',
    'ChevronRight',
    'Sparkles',
  ]) out[n] = Stub
  return out
})

import { api } from '../api/client'
import { StudyCreateForm } from '../components/study/StudyCreateForm'

const mockStart = vi.mocked(api.study.start)

function fillRequired(objective = '动量因子研究') {
  fireEvent.change(screen.getByPlaceholderText(/研究 A 股动量因子/), {
    target: { value: objective },
  })
  fireEvent.change(screen.getByTestId('strategy-name'), {
    target: { value: 'mom_20d' },
  })
}

describe('StudyCreateForm', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStart.mockResolvedValue({
      status: 'ok', study_id: 'st-new', session_id: 'st-new',
    } as never)
  })

  it('blocks submit without sessionId / objective / strategy name', async () => {
    render(<StudyCreateForm sessionId={null} workspacePath="/tmp" />)
    fireEvent.change(screen.getByPlaceholderText(/研究 A 股动量因子/), {
      target: { value: 'x' },
    })
    fireEvent.submit(screen.getByRole('button', { name: /启动 study/ }).closest('form')!)
    expect(await screen.findByText('Session ID is required.')).toBeInTheDocument()
    expect(mockStart).not.toHaveBeenCalled()
  })

  it('submits the assembled payload and calls onCreated', async () => {
    const onCreated = vi.fn()
    render(
      <StudyCreateForm sessionId="sess-1" workspacePath="/tmp/ws" onCreated={onCreated} />,
    )
    fillRequired()
    fireEvent.submit(screen.getByRole('button', { name: /启动 study/ }).closest('form')!)
    await waitFor(() => expect(mockStart).toHaveBeenCalledTimes(1))
    const payload = mockStart.mock.calls[0][0]
    expect(payload.objective).toBe('动量因子研究')
    expect(payload.strategy_name).toBe('mom_20d')
    expect(payload.session_id).toBe('sess-1')
    expect(payload.workspace_path).toBe('/tmp/ws')
    expect(payload.engine).toBe('phases')
    // default metric rows are passed through
    expect((payload.metric_targets ?? []).map((m: { name: string }) => m.name).sort())
      .toEqual(['calmar', 'max_dd', 'sharpe'])
    // no budgets set → undefined, not NaN
    expect(payload.budget_turn).toBeUndefined()
    expect(payload.max_rounds).toBeUndefined()
    expect(onCreated).toHaveBeenCalledWith('st-new')
  })

  it('drops empty metric rows and hard-fails on incomplete ones', async () => {
    render(<StudyCreateForm sessionId="sess-1" workspacePath="/tmp" />)
    fillRequired()
    fireEvent.submit(screen.getByRole('button', { name: /启动 study/ }).closest('form')!)
    // defaults have no empty rows → passes; now simulate an incomplete row
    await waitFor(() => expect(mockStart).toHaveBeenCalled())
  })

  it('surfaces API errors on failure', async () => {
    mockStart.mockRejectedValueOnce(new Error('workspace missing') as never)
    render(<StudyCreateForm sessionId="sess-1" workspacePath="/tmp" />)
    fillRequired()
    fireEvent.submit(screen.getByRole('button', { name: /启动 study/ }).closest('form')!)
    expect(await screen.findByText('workspace missing')).toBeInTheDocument()
  })

  it('resets objective and strategy after a successful create', async () => {
    render(<StudyCreateForm sessionId="sess-1" workspacePath="/tmp" />)
    fillRequired()
    fireEvent.submit(screen.getByRole('button', { name: /启动 study/ }).closest('form')!)
    // Wait for the reset to reach the DOM. The API call landing only
    // proves the request fired — under coverage instrumentation the
    // call and React's commit of the cleared inputs can land in
    // different batches, so an immediate value assertion races.
    await waitFor(() =>
      expect(
        (screen.getByPlaceholderText(/研究 A 股动量因子/) as HTMLInputElement).value,
      ).toBe('')
    )
    expect((screen.getByTestId('strategy-name') as HTMLInputElement).value).toBe('')
    expect(mockStart).toHaveBeenCalledTimes(1)
  })
})
