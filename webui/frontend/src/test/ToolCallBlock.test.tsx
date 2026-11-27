import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ToolCallBlock } from '../components/chat/ToolCallBlock'
import { chatUiConfig } from '../components/chat/chatUiConfig'
import type { ToolCallPart } from '../stores/chat'

// react-syntax-highlighter is mocked in test/setup.ts
const baseTc: ToolCallPart = {
  type: 'tool_call',
  id: 'tc1',
  name: 'search_alpha',
  arguments: JSON.stringify({ query: 'momentum', top_k: 10 }),
  status: 'done',
  result: JSON.stringify({ results: [{ name: 'alpha1', ic: 0.045 }] }),
}

describe('ToolCallBlock', () => {
  it('renders single-line summary chip with name + args preview', () => {
    render(<ToolCallBlock toolCall={baseTc} />)
    expect(screen.getByText('search_alpha')).toBeTruthy()
    // Args preview shows "query: momentum, top_k: 10"
    expect(screen.getByText(/query: momentum/)).toBeTruthy()
    // Result not visible until expanded
    expect(screen.queryByText(/alpha1/)).toBeNull()
  })

  it('shows markdown-rendered args + result when expanded', () => {
    render(<ToolCallBlock toolCall={baseTc} />)
    // Click the summary chip
    const summary = screen.getByRole('button', { name: /search_alpha/ }).closest('[role="button"]') as HTMLElement
    fireEvent.click(summary)
    // Now "Arguments" and "Result" should be rendered as markdown
    expect(screen.getByText(/Arguments/)).toBeTruthy()
    expect(screen.getByText(/Result/)).toBeTruthy()
  })

  it('handles invalid JSON args gracefully', () => {
    const bad: ToolCallPart = {
      ...baseTc,
      arguments: 'not json{{',
      result: undefined,
    }
    render(<ToolCallBlock toolCall={bad} />)
    // Should not throw; preview shows raw text truncated
    expect(screen.getByText('search_alpha')).toBeTruthy()
  })

  it('shows spinner when status is running', () => {
    const running: ToolCallPart = { ...baseTc, status: 'running' }
    const { container } = render(<ToolCallBlock toolCall={running} />)
    // Loader2 has animate-spin class on its container icon
    const spinner = container.querySelector('.animate-spin')
    expect(spinner).toBeTruthy()
  })
})
describe('ToolCallBlock — run_backtest summary', () => {
  const rbTc: ToolCallPart = {
    type: 'tool_call',
    id: 'tc-rb',
    name: 'run_backtest',
    arguments: JSON.stringify({ strategy_name: 'a_share_momentum_v4' }),
    status: 'done',
    result: JSON.stringify({
      status: 'ok',
      run: 'run_0002',
      metrics: {
        ann_return: 0.1276,
        sharpe: 0.9185,
        max_drawdown: -0.14,
        calmar: 0.9,
      },
    }),
  }

  it('summarizes nested metrics (ann_return/sharpe/max_drawdown)', () => {
    render(<ToolCallBlock toolCall={rbTc} />)
    expect(screen.getByText(/Sharpe=0\.92/)).toBeTruthy()
    expect(screen.getByText(/MaxDD=-14\.00%/)).toBeTruthy()
    expect(screen.getByText(/年化=12\.76%/)).toBeTruthy()
  })
})

// ── P0-3: toolCall config drives dangerous-tools list + visibility ──
// The config is a shared singleton; mutations are restored per test.

const toolCallBackup = JSON.parse(JSON.stringify(chatUiConfig.toolCall))
afterEach(() => {
  // Fresh clone each time: a direct re-assign would alias the backup to the
  // singleton after the first restore, letting mutations leak across tests.
  chatUiConfig.toolCall = JSON.parse(JSON.stringify(toolCallBackup))
})

describe('ToolCallBlock — P0-3 config wiring', () => {
  const errTc: ToolCallPart = { ...baseTc, status: 'error' }
  const rbTc: ToolCallPart = {
    type: 'tool_call',
    id: 'tc-rb-p0',
    name: 'run_backtest',
    arguments: '{}',
    status: 'done',
  }

  it('dangerousTools is the single source: a config-listed non-default tool shows the badge', () => {
    chatUiConfig.toolCall.dangerousTools = ['search_alpha']
    render(<ToolCallBlock toolCall={baseTc} />)
    expect(screen.getByTestId('dangerous-tool-badge')).toBeTruthy()
  })

  it('a default-listed tool loses its badge once removed from config', () => {
    chatUiConfig.toolCall.dangerousTools = ['search_alpha']
    render(<ToolCallBlock toolCall={rbTc} />)
    expect(screen.queryByTestId('dangerous-tool-badge')).toBeNull()
  })

  it('showDangerousBadge=false hides the badge even for configured tools', () => {
    chatUiConfig.toolCall.dangerousTools = ['search_alpha']
    chatUiConfig.toolCall.visibility.showDangerousBadge = false
    render(<ToolCallBlock toolCall={baseTc} />)
    expect(screen.queryByTestId('dangerous-tool-badge')).toBeNull()
  })

  it('showArgsPreview=false hides the args preview', () => {
    chatUiConfig.toolCall.visibility.showArgsPreview = false
    const { queryByText } = render(<ToolCallBlock toolCall={baseTc} />)
    expect(queryByText(/query: momentum/)).toBeNull()
  })

  it('showDuration=false hides the runtime', () => {
    chatUiConfig.toolCall.visibility.showDuration = false
    const { container } = render(<ToolCallBlock toolCall={baseTc} startTime={Date.now() - 5000} />)
    expect(container.textContent).not.toMatch(/\d+(\.\d+)?s/)
  })

  it('showRetry=false hides the retry affordance on error', () => {
    chatUiConfig.toolCall.visibility.showRetry = false
    const onRetry = () => {}
    const { queryByTitle } = render(<ToolCallBlock toolCall={errTc} onRetry={onRetry} />)
    expect(queryByTitle('重试')).toBeNull()
  })

  it('showRetry=true (default) keeps the retry affordance on error', () => {
    chatUiConfig.toolCall.visibility.showRetry = true
    const onRetry = () => {}
    const { queryByTitle } = render(<ToolCallBlock toolCall={errTc} onRetry={onRetry} />)
    expect(queryByTitle('重试')).toBeTruthy()
  })

  it('showCopy=false hides the copy button', () => {
    chatUiConfig.toolCall.visibility.showCopy = false
    const { queryByTitle } = render(<ToolCallBlock toolCall={baseTc} />)
    expect(queryByTitle('复制 args + result')).toBeNull()
  })
})
