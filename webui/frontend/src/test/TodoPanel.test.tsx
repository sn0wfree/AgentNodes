// TodoPanel (opencode-style session-todo-dock) — bottom panel above the
// composer with a collapsible header and a checkbox list.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { TodoPanel } from '../components/chat/TodoPanel'
import { useTodoStore } from '../stores/todo'

vi.mock('lucide-react', async () => {
  const Stub = () => null
  return {
    ChevronDown: Stub,
    Check: Stub,
    X: Stub,
    ListChecks: Stub,
    Circle: Stub,
    CheckCircle2: Stub,
    Clock: Stub,
  }
})

const TODOS = [
  { id: 't1', content: '加载数据', status: 'in_progress' as const },
  { id: 't2', content: '计算因子', status: 'pending' as const },
  { id: 't3', content: '回测验证', status: 'completed' as const },
]

function header() {
  return document.querySelector(
    '[data-action="session-todo-toggle"]'
  ) as HTMLElement | null
}
function chevron() {
  return document.querySelector(
    '[data-action="session-todo-toggle-button"]'
  ) as HTMLElement | null
}
function list() {
  return document.querySelector('[data-slot="session-todo-list"]') as HTMLElement | null
}
function preview() {
  return document.querySelector('[data-slot="session-todo-preview"]') as HTMLElement | null
}

beforeEach(() => {
  useTodoStore.setState({ todos: [], expanded: false })
  sessionStorage.clear()
})

describe('TodoPanel (opencode-style bottom dock)', () => {
  it('renders nothing when there are no todos', () => {
    const { container } = render(<TodoPanel />)
    expect(container.querySelector('[data-testid="todo-panel"]')).toBeNull()
  })

  it('renders the floating card above the composer with progress label', () => {
    useTodoStore.setState({ todos: TODOS, expanded: false })
    render(<TodoPanel />)
    expect(screen.getByText(/已完成 1 个任务/)).toBeTruthy()
    expect(screen.getByText(/共 3 个/)).toBeTruthy()
  })

  it('shows the active todo preview in the collapsed header', () => {
    useTodoStore.setState({ todos: TODOS, expanded: false })
    render(<TodoPanel />)
    expect(preview()?.textContent).toBe('加载数据')
  })

  it('renders the checkbox rows when expanded (visible in list, not just header)', () => {
    useTodoStore.setState({ todos: TODOS, expanded: true })
    render(<TodoPanel />)
    const listEl = list()!
    expect(listEl.textContent).toContain('加载数据')
    expect(listEl.textContent).toContain('计算因子')
    expect(listEl.textContent).toContain('回测验证')
  })

  it('marks completed items with line-through + muted text', () => {
    useTodoStore.setState({ todos: TODOS, expanded: true })
    render(<TodoPanel />)
    const completed = screen.getByText('回测验证')
    expect(completed.className).toMatch(/line-through/)
    // 配色统一阶段 2：text-slate-500 → 语义类 text-muted（paletteTokens 契约）
    expect(completed.className).toMatch(/text-muted/)
  })

  it('renders the in_progress row with a pulse-dot indicator (data-in-progress)', () => {
    useTodoStore.setState({ todos: TODOS, expanded: true })
    render(<TodoPanel />)
    const inProgressRow = document.querySelector('[data-in-progress]')
    expect(inProgressRow).not.toBeNull()
    expect(inProgressRow?.getAttribute('data-state')).toBe('in_progress')
  })

  it('renders the completed row with the completed data-state', () => {
    useTodoStore.setState({ todos: TODOS, expanded: true })
    render(<TodoPanel />)
    const rows = document.querySelectorAll('[data-state="completed"]')
    expect(rows.length).toBe(1)
    expect(rows[0].textContent).toContain('回测验证')
  })

  it('toggles expanded when the header is clicked', () => {
    useTodoStore.setState({ todos: TODOS, expanded: false })
    render(<TodoPanel />)
    fireEvent.click(header()!)
    expect(useTodoStore.getState().expanded).toBe(true)
    fireEvent.click(header()!)
    expect(useTodoStore.getState().expanded).toBe(false)
  })

  it('toggles expanded when the chevron button is clicked (without bubbling)', () => {
    useTodoStore.setState({ todos: TODOS, expanded: false })
    render(<TodoPanel />)
    fireEvent.click(chevron()!)
    expect(useTodoStore.getState().expanded).toBe(true)
  })

  it('chevron has data-collapsed reflecting state', () => {
    useTodoStore.setState({ todos: TODOS, expanded: true })
    render(<TodoPanel />)
    expect(chevron()?.getAttribute('data-collapsed')).toBe('false')
    useTodoStore.setState({ expanded: false })
    render(<TodoPanel />)
    expect(chevron()?.getAttribute('data-collapsed')).toBe('true')
  })

  it('animates the list via grid-template-rows (0fr when collapsed, 1fr when expanded)', () => {
    useTodoStore.setState({ todos: TODOS, expanded: false })
    const { container } = render(<TodoPanel />)
    const gridWrap = container.querySelector('[data-testid="todo-panel"] .grid') as HTMLElement
    expect(gridWrap.style.gridTemplateRows).toBe('0fr')
    useTodoStore.setState({ expanded: true })
    const { container: c2 } = render(<TodoPanel />)
    const gridWrap2 = c2.querySelector('[data-testid="todo-panel"] .grid') as HTMLElement
    expect(gridWrap2.style.gridTemplateRows).toBe('1fr')
  })

  it('marks the list aria-hidden when collapsed, visible when expanded', () => {
    useTodoStore.setState({ todos: TODOS, expanded: false })
    render(<TodoPanel />)
    expect(list()?.getAttribute('aria-hidden')).toBe('true')
    useTodoStore.setState({ expanded: true })
    render(<TodoPanel />)
    expect(list()?.getAttribute('aria-hidden')).toBe('false')
  })

  it('falls back to last-completed → first when no in_progress or pending', () => {
    useTodoStore.setState({
      todos: [
        { id: 'a', content: 'old-done', status: 'completed' },
        { id: 'b', content: 'last-done', status: 'completed' },
      ],
      expanded: false,
    })
    render(<TodoPanel />)
    expect(preview()?.textContent).toBe('last-done')
  })
})