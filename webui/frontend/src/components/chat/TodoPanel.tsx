import { ChevronDown, Check } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTodoStore, type TodoItem } from '../../stores/todo'

/**
 * Todo panel — opencode-style session-todo-dock.
 *
 * Sits as a floating card above the composer. When collapsed (default),
 * the 42px header shows the progress count + a one-line preview of the
 * currently active todo. Clicking the header (or the chevron button)
 * expands the panel to reveal the full checkbox list. When every todo
 * reaches a terminal state, the SSE handler clears the list after a
 * short delay so the panel disappears gracefully.
 */
export function TodoPanel() {
  const todos = useTodoStore((s) => s.todos)
  const expanded = useTodoStore((s) => s.expanded)
  const toggleExpanded = useTodoStore((s) => s.toggleExpanded)

  if (todos.length === 0) return null

  const total = todos.length
  const done = todos.filter(
    (t) => t.status === 'completed' || t.status === 'cancelled'
  ).length
  const active = pickActive(todos)
  const preview = active?.content ?? ''

  return (
    <div
      className="pointer-events-auto mx-auto w-full max-w-3xl px-3 pb-2"
      data-testid="todo-panel"
    >
      <div
        className="overflow-hidden rounded-xl border border-subtle bg-background/95 shadow-lg backdrop-blur"
        data-component="session-todo-dock"
      >
        {/* Header row */}
        <div
          role="button"
          tabIndex={0}
          aria-label={expanded ? '折叠任务清单' : '展开任务清单'}
          onClick={toggleExpanded}
          onKeyDown={(e) => {
            if (e.key !== 'Enter' && e.key !== ' ') return
            e.preventDefault()
            toggleExpanded()
          }}
          className="flex h-[42px] cursor-pointer items-center gap-2 overflow-visible pl-4 pr-2 select-none hover:bg-surface"
          data-action="session-todo-toggle"
          data-collapsed={expanded ? 'false' : 'true'}
        >
          <span className="shrink-0 text-[13px] font-normal leading-5 tracking-[-0.04px] text-muted">
            已完成 {done} 个任务（共 {total} 个）
          </span>
          {preview && (
            <div
              className="ml-1 min-w-0 flex-1 truncate text-[13px] font-normal leading-5 tracking-[-0.04px] text-muted"
              data-slot="session-todo-preview"
            >
              {preview}
            </div>
          )}
          <button
            type="button"
            aria-label={expanded ? '折叠任务清单' : '展开任务清单'}
            data-action="session-todo-toggle-button"
            data-collapsed={expanded ? 'false' : 'true'}
            onMouseDown={(e) => e.preventDefault()}
            onClick={(e) => {
              e.stopPropagation()
              toggleExpanded()
            }}
            className="ml-auto flex h-7 w-7 items-center justify-center rounded text-muted hover:bg-surface-raised hover:text-secondary"
          >
            <ChevronDown
              className={`h-4 w-4 transition-transform duration-300 ${
                expanded ? 'rotate-180' : 'rotate-0'
              }`}
            />
          </button>
        </div>

        {/* Animated list area (grid-template-rows trick for height auto) */}
        <div
          className="grid transition-[grid-template-rows] duration-300 ease-out"
          style={{ gridTemplateRows: expanded ? '1fr' : '0fr' }}
        >
          <div className="overflow-hidden">
            <TodoList todos={todos} hidden={!expanded} />
          </div>
        </div>
      </div>
    </div>
  )
}

function TodoList({ todos, hidden }: { todos: TodoItem[]; hidden: boolean }) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [stuck, setStuck] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const onScroll = () => setStuck(el.scrollTop > 0)
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  return (
    <div className="relative">
      <div
        ref={ref}
        aria-hidden={hidden}
        className="flex max-h-[168px] flex-col gap-1.5 overflow-y-auto px-3 pb-[44px]"
        style={{ scrollbarWidth: 'none', overflowAnchor: 'none' }}
        data-slot="session-todo-list"
      >
        {todos.map((t) => (
          <TodoRow key={t.id} todo={t} />
        ))}
      </div>
      <div
        className="pointer-events-none absolute top-0 right-0 left-0 h-4 bg-gradient-to-b from-slate-900 to-transparent transition-opacity duration-150"
        style={{ opacity: stuck ? 1 : 0 }}
      />
    </div>
  )
}

function TodoRow({ todo }: { todo: TodoItem }) {
  const checked = todo.status === 'completed' || todo.status === 'cancelled'
  const inProgress = todo.status === 'in_progress'
  const labelClass = checked
    ? 'text-muted line-through'
    : inProgress
      ? 'text-foreground'
      : 'text-foreground/90'

  return (
    <label
      className="flex items-start gap-2 py-0.5"
      data-state={todo.status}
      data-in-progress={inProgress ? '' : undefined}
    >
      <span
        className={`mt-0.5 inline-flex h-4 w-4 flex-shrink-0 items-center justify-center rounded border ${
          checked
            ? 'border-slate-500 bg-surface-raised'
            : inProgress
              ? 'border-slate-500 bg-surface-raised'
              : 'border bg-transparent'
        }`}
        aria-hidden="true"
      >
        {checked && <Check className="h-3 w-3 text-white" strokeWidth={3} />}
        {inProgress && (
          <span className="block h-1.5 w-1.5 animate-pulse rounded-full bg-warning" />
        )}
      </span>
      <span
        className={`min-w-0 flex-1 break-words text-[13px] leading-5 transition-colors duration-200 ${labelClass}`}
      >
        {todo.content}
      </span>
    </label>
  )
}

function pickActive(todos: TodoItem[]): TodoItem | undefined {
  return (
    todos.find((t) => t.status === 'in_progress') ??
    todos.find((t) => t.status === 'pending') ??
    [...todos].reverse().find((t) => t.status === 'completed' || t.status === 'cancelled') ??
    todos[0]
  )
}

// Re-export for consumers that want to render their own todo UI
export type { TodoStatus, TodoItem } from '../../stores/todo'