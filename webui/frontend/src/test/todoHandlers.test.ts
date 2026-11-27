// todo_updated SSE handler — first-seen auto-expand, terminal-state auto-close.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useTodoStore } from '../stores/todo'
import { todoUpdated } from '../hooks/sse/todoHandlers'
import { useChatStore } from '../stores/chat'

function ctx(sessionId = 's-handler-test') {
  return {
    sessionId,
    messageId: 'm-1',
    updateMessage: () => {},
    applyTextDelta: () => {},
    state: useChatStore.getState(),
  } as never
}

beforeEach(() => {
  useTodoStore.setState({ todos: [], expanded: false })
  sessionStorage.clear()
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('todoUpdated handler', () => {
  it('replaces todos and auto-expands on first event for the session', () => {
    todoUpdated(
      {
        todos: [
          { id: 't1', content: '加载数据', status: 'in_progress' },
          { id: 't2', content: '计算因子', status: 'pending' },
        ],
      },
      ctx(),
    )
    const s = useTodoStore.getState()
    expect(s.todos).toHaveLength(2)
    expect(s.todos[0].content).toBe('加载数据')
    expect(s.expanded).toBe(true)
  })

  it('ignores malformed payloads', () => {
    todoUpdated({}, ctx())
    todoUpdated({ todos: 'not-an-array' }, ctx())
    todoUpdated({ todos: [{ id: 1, status: 'x' }] }, ctx())
    expect(useTodoStore.getState().todos).toEqual([])
    expect(useTodoStore.getState().expanded).toBe(false)
  })

  it('filters items without valid id/content', () => {
    todoUpdated(
      {
        todos: [
          { id: 't1', content: 'ok', status: 'pending' },
          { id: 't2', status: 'pending' },
          { content: 'no-id', status: 'pending' },
        ],
      },
      ctx(),
    )
    const s = useTodoStore.getState()
    expect(s.todos).toHaveLength(1)
    expect(s.todos[0].id).toBe('t1')
    expect(s.expanded).toBe(true)
  })

  it('does not auto-expand again once the session has been seen', () => {
    const session = 's-once'
    // First event expands
    todoUpdated({ todos: [{ id: 'a', content: 'a', status: 'pending' }] }, ctx(session))
    expect(useTodoStore.getState().expanded).toBe(true)
    // User collapses
    useTodoStore.getState().setExpanded(false)
    // Second event: respect the collapse
    todoUpdated({ todos: [{ id: 'b', content: 'b', status: 'in_progress' }] }, ctx(session))
    expect(useTodoStore.getState().expanded).toBe(false)
  })

  it('schedules an auto-close (400ms) when every todo is terminal', () => {
    todoUpdated(
      {
        todos: [
          { id: 'a', content: 'a', status: 'completed' },
          { id: 'b', content: 'b', status: 'cancelled' },
        ],
      },
      ctx('s-done'),
    )
    expect(useTodoStore.getState().todos).toHaveLength(2)
    expect(useTodoStore.getState().expanded).toBe(true)
    // Before the delay, the panel is still visible
    vi.advanceTimersByTime(200)
    expect(useTodoStore.getState().todos.length).toBe(2)
    // After 400ms the panel clears itself
    vi.advanceTimersByTime(250)
    expect(useTodoStore.getState().todos).toEqual([])
    expect(useTodoStore.getState().expanded).toBe(false)
  })

  it('cancels a pending auto-close if a new (non-terminal) update arrives', () => {
    // First, all done → schedules close
    todoUpdated(
      { todos: [{ id: 'a', content: 'a', status: 'completed' }] },
      ctx('s-cancel'),
    )
    vi.advanceTimersByTime(100)
    // Then a fresh event with non-terminal status
    todoUpdated(
      { todos: [{ id: 'a', content: 'a', status: 'in_progress' }] },
      ctx('s-cancel'),
    )
    // Past the original deadline — todos should NOT have been cleared
    vi.advanceTimersByTime(500)
    expect(useTodoStore.getState().todos).toHaveLength(1)
    expect(useTodoStore.getState().todos[0].status).toBe('in_progress')
  })
})