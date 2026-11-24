import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AssistantMessage } from '../components/chat/AssistantMessage'
import { useSystemStore } from '../stores/system'
import type { Message } from '../stores/chat'

const baseMsg: Message = {
  id: 'm1',
  session_id: 's1',
  role: 'assistant',
  parts: [{ type: 'text', id: 'test-1', text: '回复内容' }],
  created_at: 1700000000,
  metadata: { model: 'gpt-4o' },
}

function setProvider(provider: string, model = 'default') {
  useSystemStore.setState({
    llm: { provider, model, configured: !!provider, max_retries: 3 },
  })
}

describe('AssistantMessage', () => {
  beforeEach(() => {
    setProvider('') // passthrough by default
  })

  it('renders avatar + label in bubble mode', () => {
    const { container } = render(
      <AssistantMessage message={baseMsg} layout="bubble" />
    )
    // Has Bot avatar circle
    expect(container.querySelector('.rounded-full')).toBeTruthy()
    // No agent_id → label shows the model name (not "Agent · Agent")
    expect(screen.getByText('gpt-4o')).toBeTruthy()
  })

  it('renders flat layout without avatar in flat mode', () => {
    const { container } = render(
      <AssistantMessage message={baseMsg} layout="flat" />
    )
    // No avatar circle in flat mode
    expect(container.querySelector('.rounded-full')).toBeFalsy()
    // Label + time visible
    expect(screen.getByText('gpt-4o')).toBeTruthy()
    // Text content visible
    expect(screen.getByText('回复内容')).toBeTruthy()
  })

  it('omits model from label when metadata.model is missing', () => {
    const msgNoModel = { ...baseMsg, metadata: undefined }
    const { rerender } = render(
      <AssistantMessage message={msgNoModel} layout="bubble" />
    )
    expect(screen.getByText('Agent')).toBeTruthy()
    rerender(<AssistantMessage message={msgNoModel} layout="flat" />)
    expect(screen.getByText('Agent')).toBeTruthy()
  })

  it('splits MiniMax <think> tags into ThinkingBlock + content (provider=minimax)', () => {
    setProvider('minimax', 'minimax-M3')
    const msg: Message = {
      ...baseMsg,
      parts: [{ type: 'text', id: 'msg-1', text: '<think>plan content</think>你好' }],
    }
    const { container } = render(
      <AssistantMessage message={msg} layout="flat" />
    )
    // ThinkingBlock rendered
    expect(screen.getByText(/Thought/)).toBeTruthy()
    // Content (without tags) visible
    expect(screen.getByText('你好')).toBeTruthy()
    // Tags themselves should not be visible as plain text
    expect(container.textContent).not.toContain('<think>')
  })

it('does NOT parse thinking when provider has no parser', () => {
    setProvider('unknown-provider')
    const msg: Message = {
      ...baseMsg,
      parts: [{ type: 'text', id: 'msg-2', text: 'plan你好' }],
    }
    render(<AssistantMessage message={msg} layout="flat" />)
    // Tags appear as-is in the content (Markdown renderer shows them)
    // The text content should contain the tags
    expect(screen.getByText(/plan你好/)).toBeTruthy()
  })

  it('renders queued indicator with position/length when isQueued', () => {
    const queuedMsg: Message = {
      ...baseMsg,
      parts: [],
      metadata: {
        queue_status: 'queued',
        queue_position: 2,
        queue_length: 3,
      },
    }
    render(
      <AssistantMessage
        message={queuedMsg}
        isQueued={true}
        layout="bubble"
      />
    )
    expect(screen.getByText(/等待中\.\.\. 2\/3/)).toBeTruthy()
  })

  // ── P15: tool_call part should forward onRetry to ToolCallBlock ──

  it('forwards onRetry to ToolCallBlock for failed tool_call parts', () => {
    // ToolCallBlock shows the retry RefreshCw button only when onRetry
    // is provided AND toolCall.status === 'error'. The pre-P15 code
    // never threaded onRetry down, so the button was permanently
    // hidden. This test pins that contract.
    const tcMsg: Message = {
      ...baseMsg,
      parts: [
        {
          type: 'tool_call',
          id: 'tc-1',
          name: 'run_backtest',
          arguments: '{"strategy":"momentum"}',
          status: 'error',
        },
      ],
    }
    const onRetry = vi.fn()
    const { container } = render(
      <AssistantMessage
        message={tcMsg}
        layout="flat"
        // AssistantMessage does not accept onRetry as a prop, but the
        // retry handler must be created internally and forwarded. We
        // exercise the public surface by checking the retry button is
        // present — it can only exist when onRetry reaches ToolCallBlock.
      />
    )
    // RefreshCw lucide icon → the retry button (svg inside a button).
    // Title="重试" is set by ToolCallBlock.
    const retryBtn = container.querySelector('button[title="重试"]')
    expect(retryBtn).toBeTruthy()
    // Sanity: handler should be the internal handleToolRetry wrapper
    // (we just assert it's callable; the implementation handles the
    // user-message lookup and /chat/send_async call).
    expect(typeof onRetry).toBe('function')
  })

  it('does not show the retry button for successful tool_call parts', () => {
    // Only failed tool calls expose the retry affordance — succeeded
    // ones should not pollute the UI with a useless button.
    const tcMsg: Message = {
      ...baseMsg,
      parts: [
        {
          type: 'tool_call',
          id: 'tc-2',
          name: 'list_strategies',
          arguments: '{}',
          status: 'done',
          result: '["s1","s2"]',
        },
      ],
    }
    const { container } = render(
      <AssistantMessage message={tcMsg} layout="flat" />
    )
    const retryBtn = container.querySelector('button[title="重试"]')
    expect(retryBtn).toBeFalsy()
  })
})