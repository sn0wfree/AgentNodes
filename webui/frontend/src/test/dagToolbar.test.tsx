// DAGToolbar — workflow control bar: status badge + 4 action buttons
// mapped from the 5 execution states.

import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { DAGToolbar } from '../components/workflow/DAGToolbar'

vi.mock('lucide-react', () => {
  const Stub = () => null
  return { Play: Stub, Pause: Stub, RotateCcw: Stub, Workflow: Stub }
})

describe('DAGToolbar', () => {
  it('renders the workflow name and status label', () => {
    render(<DAGToolbar workflowName="factor_research" status="idle" />)
    expect(screen.getByText('factor_research')).toBeInTheDocument()
    expect(screen.getByText('就绪')).toBeInTheDocument()
  })

  it('shows the Start button only when idle', () => {
    const { rerender } = render(
      <DAGToolbar workflowName="wf" status="idle" onStart={() => {}} />
    )
    expect(screen.getByRole('button', { name: /启动/ })).toBeInTheDocument()

    rerender(<DAGToolbar workflowName="wf" status="running" onPause={() => {}} />)
    expect(screen.queryByRole('button', { name: /启动/ })).toBeNull()
  })

  it('shows Pause when running and triggers onPause', () => {
    const onPause = vi.fn()
    render(<DAGToolbar workflowName="wf" status="running" onPause={onPause} />)
    fireEvent.click(screen.getByRole('button', { name: /暂停/ }))
    expect(onPause).toHaveBeenCalledTimes(1)
  })

  it('shows Resume when paused and triggers onResume', () => {
    const onResume = vi.fn()
    render(<DAGToolbar workflowName="wf" status="paused" onResume={onResume} />)
    fireEvent.click(screen.getByRole('button', { name: /恢复/ }))
    expect(onResume).toHaveBeenCalledTimes(1)
  })

  it('shows Reset when completed and triggers onReset', () => {
    const onReset = vi.fn()
    render(<DAGToolbar workflowName="wf" status="completed" onReset={onReset} />)
    fireEvent.click(screen.getByRole('button', { name: /重置/ }))
    expect(onReset).toHaveBeenCalledTimes(1)
  })

  it('shows Reset when failed and triggers onReset', () => {
    const onReset = vi.fn()
    render(<DAGToolbar workflowName="wf" status="failed" onReset={onReset} />)
    fireEvent.click(screen.getByRole('button', { name: /重置/ }))
    expect(onReset).toHaveBeenCalledTimes(1)
  })

  it('does not call any callback when none is provided (button is still present)', () => {
    // The Start button is rendered for status=idle even without onStart
    // (clicking it would no-op because onStart is undefined). This
    // documents the intentional behavior.
    const onClick = vi.fn()
    render(<DAGToolbar workflowName="wf" status="idle" />)
    const btn = screen.getByRole('button', { name: /启动/ })
    expect(() => fireEvent.click(btn)).not.toThrow()
    expect(onClick).not.toHaveBeenCalled()
  })

  it('maps status to the right color class', () => {
    const { rerender, container } = render(
      <DAGToolbar workflowName="wf" status="running" />
    )
    expect(container.querySelector('.text-info-fg')).toBeTruthy()
    rerender(<DAGToolbar workflowName="wf" status="completed" />)
    expect(container.querySelector('.text-success-fg')).toBeTruthy()
    rerender(<DAGToolbar workflowName="wf" status="failed" />)
    expect(container.querySelector('.text-danger-fg')).toBeTruthy()
  })
})