// T4.3a — Composer: the chat input. Highest-value contracts are the
// send path (optimistic user message, attempt wiring, temp-id rename,
// failure rollback), the streaming cancel path, draft persistence, slash
// menu detection, and the paste/upload guards.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

// The component imports `api` from src/api/client; from src/test/ that is
// '../api/client'.
vi.mock('../api/client', () => ({
  api: { post: vi.fn(), get: vi.fn() },
}))

// The composer reads its session from a context hook.
const sessionMock = vi.hoisted(() => ({ id: 'sess-1' as string | null }))
vi.mock('../contexts/ChatSessionContext', () => ({
  useChatSessionId: () => sessionMock.id,
}))

// Child surfaces that pull their own stores/charts are out of scope here.
vi.mock('../components/chat/ComposerToolbar', () => ({
  ComposerToolbar: () => <div data-testid="toolbar" />,
}))
vi.mock('../components/chat/ComposerStatusBar', () => ({
  ComposerStatusBar: () => <div data-testid="statusbar" />,
}))
vi.mock('../components/chat/SlashCommandMenu', () => ({
  SlashCommandMenu: ({ query }: { query: string }) => (
    <div data-testid="slash-menu">{query}</div>
  ),
}))

import { api } from '../api/client'
import { Composer } from '../components/chat/Composer'
import { useChatStore } from '../stores/chat'
import { useToastStore } from '../stores/toast'
import { useModeStore } from '../stores/mode'
import { useModelStore } from '../stores/model'
import { useThinkingPrefStore } from '../stores/thinkingPref'
import { usePersonaStore } from '../stores/personas'

// The textarea has no accessible role/name, so locate it by tag.
const textarea = () => document.querySelector('textarea') as HTMLTextAreaElement

beforeEach(() => {
  sessionMock.id = 'sess-1'
  localStorage.clear()
  useChatStore.setState({
    messages: new Map(),
    streamingMessageId: null,
    activeAttemptId: null,
  } as never)
  useToastStore.setState({ toasts: [] } as never)
  useModeStore.setState({ mode: 'build' } as never)
  useModelStore.setState({ sessionModels: {} } as never)
  useThinkingPrefStore.setState({ thinkingMode: 'auto' } as never)
  usePersonaStore.setState({ sessionPersona: {}, loaded: true } as never)
  vi.mocked(api.post).mockReset()
  vi.mocked(api.post).mockResolvedValue({ message_id: 'm-1', status: 'queued' } as never)
})

describe('Composer', () => {
  it('renders the input and hides the send affordance until there is text', () => {
    render(<Composer />)
    expect(textarea()).toBeInTheDocument()
    fireEvent.change(textarea(), { target: { value: 'hello' } })
    expect(textarea()).toHaveValue('hello')
  })

  it('does not send an empty draft', async () => {
    render(<Composer />)
    fireEvent.keyDown(textarea(), { key: 'Enter' })
    expect(api.post).not.toHaveBeenCalled()
  })

  it('does not send without a session', async () => {
    sessionMock.id = null
    render(<Composer />)
    fireEvent.change(textarea(), { target: { value: 'hi' } })
    fireEvent.keyDown(textarea(), { key: 'Enter' })
    expect(api.post).not.toHaveBeenCalled()
  })

  it('sends on Enter, clears the draft and adds an optimistic user message', async () => {
    render(<Composer />)
    fireEvent.change(textarea(), { target: { value: 'hello world' } })
    fireEvent.keyDown(textarea(), { key: 'Enter' })

    await waitFor(() => expect(api.post).toHaveBeenCalled())
    const [path, body] = vi.mocked(api.post).mock.calls[0]!
    expect(path).toBe('/chat/send_async')
    expect(body).toMatchObject({
      session_id: 'sess-1',
      content: 'hello world',
      mode: 'build',
      thinking: 'auto',
    })
    expect(textarea()).toHaveValue('')

    const msgs = Array.from(useChatStore.getState().messages.values())
    expect(msgs).toHaveLength(1)
    expect(msgs[0]!.role).toBe('user')
  })

  it('Shift+Enter inserts a newline instead of sending', () => {
    render(<Composer />)
    fireEvent.change(textarea(), { target: { value: 'line1' } })
    fireEvent.keyDown(textarea(), { key: 'Enter', shiftKey: true })
    expect(api.post).not.toHaveBeenCalled()
  })

  it('stores attempt_id from the response for cancel support', async () => {
    vi.mocked(api.post).mockResolvedValue({
      message_id: 'm-1', attempt_id: 'att-9', status: 'queued',
    } as never)
    render(<Composer />)
    fireEvent.change(textarea(), { target: { value: 'hi' } })
    fireEvent.keyDown(textarea(), { key: 'Enter' })
    await waitFor(() =>
      expect(useChatStore.getState().activeAttemptId).toBe('att-9')
    )
  })

  it('renames the optimistic message to the backend user_message_id', async () => {
    vi.mocked(api.post).mockResolvedValue({
      message_id: 'srv-assistant', user_message_id: 'srv-user', status: 'queued',
    } as never)
    render(<Composer />)
    fireEvent.change(textarea(), { target: { value: 'hi' } })
    fireEvent.keyDown(textarea(), { key: 'Enter' })
    await waitFor(() => {
      const ids = Array.from(useChatStore.getState().messages.keys())
      expect(ids).toContain('srv-user')
      expect(ids.some((id) => id.startsWith('msg_'))).toBe(false)
    })
  })

  it('rolls back the optimistic message and toasts on failure', async () => {
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('boom'), { status: 500 })
    )
    render(<Composer />)
    fireEvent.change(textarea(), { target: { value: 'hi' } })
    fireEvent.keyDown(textarea(), { key: 'Enter' })

    await waitFor(() =>
      expect(useChatStore.getState().messages.size).toBe(0)
    )
    expect(useToastStore.getState().toasts.length).toBeGreaterThan(0)
    expect(textarea()).toHaveValue('hi')
  })

  it('keeps new input as well as the failed message', async () => {
    let rejectSend: (reason: Error) => void = () => {}
    vi.mocked(api.post).mockReturnValueOnce(new Promise((_, reject) => {
      rejectSend = reject
    }))
    render(<Composer />)
    fireEvent.change(textarea(), { target: { value: 'first message' } })
    fireEvent.keyDown(textarea(), { key: 'Enter' })
    fireEvent.change(textarea(), { target: { value: 'next draft' } })
    rejectSend(new Error('offline'))

    await waitFor(() => expect(textarea()).toHaveValue('first message\n\nnext draft'))
  })

  it('does not send another message while the first submission is pending', async () => {
    let resolveSend: (value: { message_id: string; status: string }) => void = () => {}
    vi.mocked(api.post).mockReturnValueOnce(new Promise((resolve) => {
      resolveSend = resolve
    }))
    render(<Composer />)
    fireEvent.change(textarea(), { target: { value: 'first message' } })
    fireEvent.keyDown(textarea(), { key: 'Enter' })
    fireEvent.change(textarea(), { target: { value: 'next draft' } })
    fireEvent.keyDown(textarea(), { key: 'Enter' })
    expect(api.post).toHaveBeenCalledTimes(1)
    expect(textarea()).toHaveValue('next draft')

    resolveSend({ message_id: 'm-1', status: 'queued' })
    await waitFor(() => expect(screen.getByTitle('发送 (Enter)')).toBeEnabled())
    fireEvent.keyDown(textarea(), { key: 'Enter' })
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2))
  })

  it('restores a failed message to its original session after switching', async () => {
    let rejectSend: (reason: Error) => void = () => {}
    vi.mocked(api.post).mockReturnValueOnce(new Promise((_, reject) => {
      rejectSend = reject
    }))
    const { rerender } = render(<Composer />)
    fireEvent.change(textarea(), { target: { value: 'original message' } })
    fireEvent.keyDown(textarea(), { key: 'Enter' })
    fireEvent.change(textarea(), { target: { value: 'unfinished follow-up' } })
    sessionMock.id = 'sess-2'
    rerender(<Composer />)
    fireEvent.change(textarea(), { target: { value: 'other session draft' } })
    rejectSend(new Error('offline'))

    await waitFor(() => expect(localStorage.getItem('sr-draft-sess-1')).toBe('original message\n\nunfinished follow-up'))
    expect(textarea()).toHaveValue('other session draft')
  })

  it('restores a failed message after the composer unmounts', async () => {
    let rejectSend: (reason: Error) => void = () => {}
    vi.mocked(api.post).mockReturnValueOnce(new Promise((_, reject) => {
      rejectSend = reject
    }))
    const { unmount } = render(<Composer />)
    fireEvent.change(textarea(), { target: { value: 'original message' } })
    fireEvent.keyDown(textarea(), { key: 'Enter' })
    unmount()
    rejectSend(new Error('offline'))

    await waitFor(() => expect(localStorage.getItem('sr-draft-sess-1')).toBe('original message'))
  })

  it('toasts a queue-full hint on 429', async () => {
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('queue full'), {
        status: 429,
        detail: { error: 'queue_full', limit: 5 },
      })
    )
    render(<Composer />)
    fireEvent.change(textarea(), { target: { value: 'hi' } })
    fireEvent.keyDown(textarea(), { key: 'Enter' })
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => t.message.includes('队列已满'))).toBe(true)
    })
  })

  it('cancels the running attempt when Enter is pressed while streaming', () => {
    const cancelSpy = vi.fn()
    useChatStore.setState({
      streamingMessageId: 'm-live',
      cancelAttempt: cancelSpy,
    } as never)
    render(<Composer />)
    fireEvent.change(textarea(), { target: { value: 'next' } })
    fireEvent.keyDown(textarea(), { key: 'Enter' })
    expect(cancelSpy).toHaveBeenCalled()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('opens the slash menu when the draft ends with "/word"', () => {
    render(<Composer />)
    fireEvent.change(textarea(), { target: { value: '/mod' } })
    expect(screen.getByTestId('slash-menu')).toHaveTextContent('/mod')
    fireEvent.change(textarea(), { target: { value: 'plain text' } })
    expect(screen.queryByTestId('slash-menu')).not.toBeInTheDocument()
  })

  it('closes the slash menu on Escape', () => {
    render(<Composer />)
    fireEvent.change(textarea(), { target: { value: '/x' } })
    expect(screen.getByTestId('slash-menu')).toBeInTheDocument()
    fireEvent.keyDown(textarea(), { key: 'Escape' })
    expect(screen.queryByTestId('slash-menu')).not.toBeInTheDocument()
  })

  it('persists the draft and restores it after remount', async () => {
    const { unmount } = render(<Composer />)
    fireEvent.change(textarea(), { target: { value: 'draft text' } })
    // Debounced write (300ms).
    await waitFor(
      () => expect(localStorage.getItem('sr-draft-sess-1')).toBe('draft text'),
      { timeout: 2000 }
    )
    unmount()

    render(<Composer />)
    await waitFor(() => expect(textarea()).toHaveValue('draft text'))
  })

  it('clears the persisted draft when the text is emptied', async () => {
    localStorage.setItem('sr-draft-sess-1', 'stale')
    render(<Composer />)
    await waitFor(() => expect(textarea()).toHaveValue('stale'))
    fireEvent.change(textarea(), { target: { value: '' } })
    await waitFor(() => expect(localStorage.getItem('sr-draft-sess-1')).toBeNull())
  })

  it('applies composeMessage before sending', async () => {
    render(<Composer composeMessage={(raw) => `[wrapped] ${raw}`} />)
    fireEvent.change(textarea(), { target: { value: 'inner' } })
    fireEvent.keyDown(textarea(), { key: 'Enter' })
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    const body = vi.mocked(api.post).mock.calls[0]![1] as { content: string }
    expect(body.content).toBe('[wrapped] inner')
  })

  it('includes persona and per-session model overrides in the payload', async () => {
    usePersonaStore.setState({
      getSessionPersona: () => 'analyst',
    } as never)
    useModelStore.setState({
      getModelForSession: () => 'glm/custom',
    } as never)
    render(<Composer />)
    fireEvent.change(textarea(), { target: { value: 'hi' } })
    fireEvent.keyDown(textarea(), { key: 'Enter' })
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    const body = vi.mocked(api.post).mock.calls[0]![1] as Record<string, unknown>
    expect(body.agent_id).toBe('analyst')
    expect(body.model).toBe('glm/custom')
  })

  it('omits agent_id when the persona is the default chat', async () => {
    usePersonaStore.setState({ getSessionPersona: () => 'chat' } as never)
    render(<Composer />)
    fireEvent.change(textarea(), { target: { value: 'hi' } })
    fireEvent.keyDown(textarea(), { key: 'Enter' })
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    const body = vi.mocked(api.post).mock.calls[0]![1] as Record<string, unknown>
    expect(body.agent_id).toBeUndefined()
  })

  // ── R-014 F3：Markdown 快捷键（8 个常驻按钮已移除，能力改由键盘提供）──
  describe('markdown shortcuts', () => {
    const select = (start: number, end: number) => {
      const ta = textarea()
      ta.focus()
      ta.setSelectionRange(start, end)
    }

    it('Ctrl+B wraps the selection in bold', () => {
      render(<Composer />)
      fireEvent.change(textarea(), { target: { value: 'hello world' } })
      select(6, 11)
      fireEvent.keyDown(textarea(), { key: 'b', ctrlKey: true, metaKey: true })
      expect(textarea().value).toBe('hello **world**')
    })

    it('Ctrl+I wraps the selection in italic', () => {
      render(<Composer />)
      fireEvent.change(textarea(), { target: { value: 'hello world' } })
      select(6, 11)
      fireEvent.keyDown(textarea(), { key: 'i', ctrlKey: true, metaKey: true })
      expect(textarea().value).toBe('hello _world_')
    })

    it('Ctrl+E wraps the selection in inline code', () => {
      render(<Composer />)
      fireEvent.change(textarea(), { target: { value: 'hello world' } })
      select(6, 11)
      fireEvent.keyDown(textarea(), { key: 'e', ctrlKey: true, metaKey: true })
      expect(textarea().value).toBe('hello `world`')
    })

    it('does not insert an empty marker pair when there is no selection', () => {
      render(<Composer />)
      fireEvent.change(textarea(), { target: { value: 'abc' } })
      select(0, 0)
      fireEvent.keyDown(textarea(), { key: 'b', ctrlKey: true, metaKey: true })
      expect(textarea().value).toBe('abc')
    })

    it('Ctrl+Shift+8 prefixes the caret line with a bullet', () => {
      render(<Composer />)
      fireEvent.change(textarea(), { target: { value: 'abc def' } })
      select(4, 4)
      fireEvent.keyDown(textarea(), { key: '*', code: 'Digit8', ctrlKey: true, metaKey: true, shiftKey: true })
      expect(textarea().value).toBe('- abc def')
    })

    it('Ctrl+Shift+8 prefixes every selected line', () => {
      render(<Composer />)
      fireEvent.change(textarea(), { target: { value: 'a\nb\nc' } })
      select(0, 5)
      fireEvent.keyDown(textarea(), { key: '*', code: 'Digit8', ctrlKey: true, metaKey: true, shiftKey: true })
      expect(textarea().value).toBe('- a\n- b\n- c')
    })

    it('Ctrl+Shift+7 numbers an ordered list sequentially', () => {
      render(<Composer />)
      fireEvent.change(textarea(), { target: { value: 'a\nb\nc' } })
      select(0, 5)
      fireEvent.keyDown(textarea(), { key: '&', code: 'Digit7', ctrlKey: true, metaKey: true, shiftKey: true })
      expect(textarea().value).toBe('1. a\n2. b\n3. c')
    })

    it('Ctrl+Shift+. prefixes the line with a quote marker', () => {
      render(<Composer />)
      fireEvent.change(textarea(), { target: { value: 'abc def' } })
      select(4, 4)
      fireEvent.keyDown(textarea(), { key: '>', code: 'Period', ctrlKey: true, metaKey: true, shiftKey: true })
      expect(textarea().value).toBe('> abc def')
    })

    it('plain typing is unaffected by the shortcut keys', () => {
      render(<Composer />)
      fireEvent.change(textarea(), { target: { value: 'abc' } })
      select(0, 3)
      fireEvent.keyDown(textarea(), { key: 'b' })
      expect(textarea().value).toBe('abc')
    })
  })
})
