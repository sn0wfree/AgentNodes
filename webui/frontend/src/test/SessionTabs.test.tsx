import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SessionTabs } from '../components/chat/SessionTabs'
import { useSessionStore } from '../stores/session'
import type { Session } from '../stores/session'

vi.mock('../api/client', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    sse: vi.fn(),
  },
}))

const makeSession = (id: string, overrides: Partial<Session> = {}): Session => ({
  id,
  user_id: 'u1',
  title: `Session ${id}`,
  created_at: Date.now() / 1000,
  updated_at: Date.now() / 1000,
  starred: false,
  tags: [],
  message_count: 0,
  archived: false,
  ...overrides,
})

describe('SessionTabs', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [],
      openSessionIds: [],
      currentSessionId: null,
      searchResults: [],
      searchOpen: false,
      searchQuery: '',
    })
    vi.clearAllMocks()
  })

  it('renders new-tab button when no sessions open', () => {
    render(<SessionTabs />)
    expect(screen.queryByTitle(/新建会话/)).toBeNull() // 加号已删，新建由侧栏承载
  })

  it('renders one tab per open session', () => {
    useSessionStore.setState({
      sessions: [
        makeSession('a', { title: '会话 A' }),
        makeSession('b', { title: '会话 B' }),
      ],
      openSessionIds: ['a', 'b'],
      currentSessionId: 'a',
    })
    render(<SessionTabs />)
    expect(screen.getByText('会话 A')).toBeInTheDocument()
    expect(screen.getByText('会话 B')).toBeInTheDocument()
  })

  it('shows star icon when starred', () => {
    useSessionStore.setState({
      sessions: [makeSession('a', { starred: true })],
      openSessionIds: ['a'],
      currentSessionId: 'a',
    })
    const { container } = render(<SessionTabs />)
    // lucide Star renders as svg with fill-slate-300
    const starSvg = container.querySelector('svg.fill-slate-300')
    expect(starSvg).toBeInTheDocument()
  })

  it('clicking tab calls switchSession', () => {
    useSessionStore.setState({
      sessions: [makeSession('a'), makeSession('b')],
      openSessionIds: ['a', 'b'],
      currentSessionId: 'a',
    })
    const switchSpy = vi.fn().mockResolvedValue(undefined)
    useSessionStore.setState({ switchSession: switchSpy })

    render(<SessionTabs />)
    fireEvent.click(screen.getByText('Session b'))
    expect(switchSpy).toHaveBeenCalledWith('b')
  })

  it('plus button is gone; new session belongs to the sidebar', () => {
    const createSpy = vi.fn().mockResolvedValue(makeSession('new'))
    useSessionStore.setState({ createNewSession: createSpy })
    // 加号已移除，createSpy 不应被调用

    render(<SessionTabs />)
    expect(screen.queryByTitle(/新建会话/)).toBeNull()
    expect(createSpy).not.toHaveBeenCalled()
  })

  it('close button calls closeSession', () => {
    useSessionStore.setState({
      sessions: [makeSession('a')],
      openSessionIds: ['a'],
      currentSessionId: 'a',
    })
    const closeSpy = vi.fn()
    useSessionStore.setState({ closeSession: closeSpy })

    const { container } = render(<SessionTabs />)
    const closeBtn = container.querySelector('button[title="关闭（保留历史）"]')
    expect(closeBtn).not.toBeNull()
    fireEvent.click(closeBtn!)
    expect(closeSpy).toHaveBeenCalledWith('a')
  })
})