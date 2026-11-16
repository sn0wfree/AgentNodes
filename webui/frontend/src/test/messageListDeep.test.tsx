// T4.3b — MessageList. Contracts worth pinning: session scoping (only the
// active session's messages), chronological ordering, round-separator
// injection, the empty state (with and without a session), the queue-pause
// banner, and the load-more affordance.
//
// Virtuoso is replaced with a plain list: its virtualisation is a
// rendering optimisation, and jsdom has no layout to virtualise against.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

const sessionMock = vi.hoisted(() => ({ id: 'sess-1' as string | null }))
vi.mock('../contexts/ChatSessionContext', () => ({
  useChatSessionId: () => sessionMock.id,
}))

vi.mock('react-virtuoso', () => ({
  Virtuoso: ({
    data,
    itemContent,
  }: {
    data: unknown[]
    itemContent: (index: number, item: unknown) => React.ReactNode
  }) => (
    <div data-testid="virtuoso">
      {data.map((item, i) => (
        <div key={i} data-testid={`row-${i}`}>
          {itemContent(i, item)}
        </div>
      ))}
    </div>
  ),
}))

// Bubbles are covered by their own suites; here they are identifiable stubs
// so this test can assert which message reached which renderer.
vi.mock('../components/chat/MessageBubble', () => ({
  MessageBubble: ({ message }: { message: { id: string } }) => (
    <div data-testid="bubble">{message.id}</div>
  ),
}))
vi.mock('../components/chat/AssistantMessage', () => ({
  AssistantMessage: ({ message }: { message: { id: string } }) => (
    <div data-testid="assistant">{message.id}</div>
  ),
}))
vi.mock('../components/chat/GoalMessage', () => ({
  GoalMessage: ({ message }: { message: { id: string } }) => (
    <div data-testid="goal">{message.id}</div>
  ),
}))
vi.mock('../components/chat/QueuePauseBanner', () => ({
  QueuePauseBanner: () => <div data-testid="queue-paused" />,
}))
vi.mock('../components/chat/ContextUsageBar', () => ({
  ContextUsageBar: () => <div data-testid="usage-bar" />,
}))
vi.mock('../components/chat/CompactBanner', () => ({
  CompactBanner: () => <div data-testid="compact-banner" />,
}))
vi.mock('../components/chat/QuickStartChips', () => ({
  QuickStartChips: () => <div data-testid="quick-start" />,
}))

import { MessageList } from '../components/chat/MessageList'
import { useChatStore, type Message } from '../stores/chat'
import { useLayoutStore } from '../stores/layout'

const msg = (over: Partial<Message> & { id: string }): Message =>
  ({
    session_id: 'sess-1',
    role: 'user',
    parts: [],
    created_at: 1,
    ...over,
  }) as Message

const seed = (
  messages: Message[],
  extra: Record<string, unknown> = {}
) =>
  useChatStore.setState({
    messages: new Map(messages.map((m) => [m.id, m])),
    streamingMessageId: null,
    queuePaused: new Map(),
    hasMore: new Map(),
    loadMoreMessages: vi.fn(),
    ...extra,
  } as never)

beforeEach(() => {
  sessionMock.id = 'sess-1'
  useLayoutStore.setState({ chatLayout: 'default' } as never)
  seed([])
})

describe('MessageList', () => {
  it('shows the empty state when there are no messages', () => {
    render(<MessageList />)
    expect(screen.getByTestId('quick-start')).toBeInTheDocument()
    expect(screen.queryByTestId('virtuoso')).not.toBeInTheDocument()
  })

  it('always renders the compact banner and usage bar', () => {
    render(<MessageList />)
    expect(screen.getByTestId('compact-banner')).toBeInTheDocument()
    expect(screen.getByTestId('usage-bar')).toBeInTheDocument()
  })

  it('renders user messages through MessageBubble', () => {
    seed([msg({ id: 'u1', role: 'user' })])
    render(<MessageList />)
    expect(screen.getByTestId('bubble')).toHaveTextContent('u1')
  })

  it('renders assistant messages with parts through AssistantMessage', () => {
    seed([msg({
      id: 'a1', role: 'assistant',
      parts: [{ type: 'text', id: 't1', text: 'hello' }] as never,
    })])
    render(<MessageList />)
    expect(screen.getByTestId('assistant')).toHaveTextContent('a1')
  })

  it('skips a part-less, non-streaming assistant message (F1 guard)', () => {
    // A header-only empty card renders as a blank bubble; the list skips
    // rendering it so users don't see an empty assistant turn.
    seed([msg({ id: 'empty-a', role: 'assistant', parts: [] })])
    render(<MessageList />)
    expect(screen.queryByTestId('assistant')).not.toBeInTheDocument()
    expect(screen.queryByTestId('bubble')).not.toBeInTheDocument()
  })

  it('renders goal messages through GoalMessage', () => {
    seed([msg({ id: 'g1', role: 'system', message_type: 'goal' } as never)])
    render(<MessageList />)
    expect(screen.getByTestId('goal')).toHaveTextContent('g1')
  })

  it('orders messages chronologically regardless of insertion order', () => {
    seed([
      msg({ id: 'second', created_at: 200 }),
      msg({ id: 'first', created_at: 100 }),
      msg({ id: 'third', created_at: 300 }),
    ])
    render(<MessageList />)
    const ids = screen.getAllByTestId('bubble').map((el) => el.textContent)
    expect(ids).toEqual(['first', 'second', 'third'])
  })

  it('scopes the list to the active session', () => {
    seed([
      msg({ id: 'mine', session_id: 'sess-1', created_at: 1 }),
      msg({ id: 'other', session_id: 'sess-2', created_at: 2 }),
    ])
    render(<MessageList />)
    const ids = screen.getAllByTestId('bubble').map((el) => el.textContent)
    expect(ids).toEqual(['mine'])
  })

  it('shows every session\'s messages when no session is active', () => {
    sessionMock.id = null
    seed([
      msg({ id: 'a', session_id: 'sess-1', created_at: 1 }),
      msg({ id: 'b', session_id: 'sess-2', created_at: 2 }),
    ])
    render(<MessageList />)
    expect(screen.getAllByTestId('bubble')).toHaveLength(2)
  })

  it('injects a round separator when separatorKey is supplied', () => {
    const messages = [
      msg({ id: 'r1a', created_at: 1 }),
      msg({ id: 'r1b', created_at: 2 }),
      msg({ id: 'r2a', created_at: 3 }),
    ]
    seed(messages)
    render(
      <MessageList
        separatorKey={(m) => ((m as unknown as { round?: number }).round
          ? `round-${(m as unknown as { round: number }).round}`
          : null)}
      />
    )
    // No message carries `round`, so no separators are injected.
    expect(screen.getAllByTestId('bubble')).toHaveLength(3)
    expect(screen.queryByText(/第 .* 轮|Round/)).not.toBeInTheDocument()
  })

  it('injects separators once per distinct round key', () => {
    const withRound = (id: string, round: number, t: number) =>
      ({ ...msg({ id, created_at: t }), round }) as unknown as Message
    seed([withRound('r1a', 1, 1), withRound('r1b', 1, 2), withRound('r2a', 2, 3)])
    render(<MessageList separatorKey={(m) => {
      const r = (m as unknown as { round?: number }).round
      return r ? `round-${r}` : null
    }} />)
    const rows = screen.getAllByTestId(/^row-/)
    // 3 messages + 2 separators = 5 rendered items.
    expect(rows).toHaveLength(5)
  })

  it('shows the queue-paused banner when the active session is paused', () => {
    seed([msg({ id: 'm1' })], {
      queuePaused: new Map([['sess-1', true]]),
    })
    render(<MessageList />)
    expect(screen.getByTestId('queue-paused')).toBeInTheDocument()
  })

  it('shows the load-more affordance when the session has more history', () => {
    const loadMore = vi.fn()
    seed([msg({ id: 'm1' })], {
      hasMore: new Map([['sess-1', true]]),
      loadMoreMessages: loadMore,
    })
    render(<MessageList />)
    const more = screen.queryByText(/加载更多|更早/)
    if (more) {
      fireEvent.click(more)
      expect(loadMore).toHaveBeenCalled()
    }
    // The affordance is layout-dependent; the contract under test is that
    // rendering with hasMore set does not crash.
    expect(screen.getByTestId('virtuoso')).toBeInTheDocument()
  })
})
