import { useState, useRef, useEffect } from 'react'
import { Search, Command, PanelRight, PanelRightClose, Pencil, Moon, Sun, Columns2, Sparkles } from 'lucide-react'
import { useSessionStore } from '../../stores/session'
import { useLayoutStore, type Density } from '../../stores/layout'
import { useThemeStore } from '../../stores/theme'
import { useThinkingPrefStore } from '../../stores/thinkingPref'
import { SSEStatus } from '../common/SSEStatus'

const DENSITY_CYCLE: Density[] = ['compact', 'comfortable', 'spacious']
const DENSITY_LABEL: Record<Density, string> = {
  compact: '紧凑',
  comfortable: '舒适',
  spacious: '宽松',
}

export function TopBar() {
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const sessions = useSessionStore((s) => s.sessions)
  const updateSessionMeta = useSessionStore((s) => s.updateSessionMeta)
  const setSearchOpen = useSessionStore((s) => s.setSearchOpen)
  const currentSession = sessions.find((s) => s.id === currentSessionId)
  const rightPanelVisible = useLayoutStore((s) => s.rightPanelVisible)
  const toggleRightPanel = useLayoutStore((s) => s.toggleRightPanel)
  const density = useLayoutStore((s) => s.density)
  const setDensity = useLayoutStore((s) => s.setDensity)
  const renameRequestId = useLayoutStore((s) => s.renameRequestId)
  const clearRenameRequest = useLayoutStore((s) => s.clearRenameRequest)
  const theme = useThemeStore((s) => s.theme)
  const toggleTheme = useThemeStore((s) => s.toggleTheme)
  const thinkingCollapsed = useThinkingPrefStore((s) => s.collapsed)
  const setThinkingCollapsed = useThinkingPrefStore((s) => s.setCollapsed)

  const [editing, setEditing] = useState(false)
  const [draftTitle, setDraftTitle] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus()
      inputRef.current.select()
    }
  }, [editing])

  const startEdit = () => {
    if (!currentSession) return
    setDraftTitle(currentSession.title)
    setEditing(true)
  }

  // 重命名入口合并：侧栏 hover 置 renameRequestId 后，等 currentSessionId
  // 切到目标会话再进入编辑态（条件满足即消费信号，清理后允许重复触发）。
  useEffect(() => {
    if (renameRequestId && renameRequestId === currentSessionId && currentSession) {
      setDraftTitle(currentSession.title)
      setEditing(true)
      clearRenameRequest()
    }
  }, [renameRequestId, currentSessionId, currentSession, clearRenameRequest])

  const commit = async () => {
    const trimmed = draftTitle.trim()
    if (trimmed && currentSession && trimmed !== currentSession.title) {
      try {
        await updateSessionMeta(currentSession.id, { title: trimmed })
      } catch (err) {
        console.error('TopBar title save failed', err)
      }
    }
    setEditing(false)
  }

  const cancel = () => setEditing(false)

  return (
    <header className="glass flex h-12 items-center justify-between gap-1 overflow-x-auto border-b border-subtle px-2 sm:px-4">
      <div className="flex items-center gap-3 min-w-0">
        {currentSession && (
          <div className="flex items-center gap-1 min-w-0">
            <span className="text-xs text-muted flex-shrink-0">/</span>
            {editing ? (
              <input
                ref={inputRef}
                value={draftTitle}
                onChange={(e) => setDraftTitle(e.target.value)}
                onBlur={() => void commit()}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    void commit()
                  } else if (e.key === 'Escape') {
                    e.preventDefault()
                    cancel()
                  }
                }}
                className="w-48 rounded border border-primary-500 bg-background px-1.5 py-0.5 text-xs text-foreground outline-none"
                maxLength={80}
              />
            ) : (
              <>
                <span
                  className="truncate text-xs text-secondary max-w-[240px]"
                  title={currentSession.title}
                >
                  {currentSession.title}
                </span>
                <button
                  onClick={startEdit}
                  className="flex h-5 w-5 items-center justify-center rounded text-muted hover:bg-surface-raised hover:text-secondary flex-shrink-0"
                  title="重命名"
                >
                  <Pencil className="h-3 w-3" />
                </button>
              </>
            )}
          </div>
        )}
        <div className="ml-2 border-l border-strong pl-2 min-w-0 overflow-hidden">
          <SSEStatus />
        </div>
      </div>
      <div className="flex items-center gap-1 sm:gap-2 flex-shrink-0">
        <button
          onClick={() => {
            const idx = DENSITY_CYCLE.indexOf(density)
            const next = DENSITY_CYCLE[(idx + 1) % DENSITY_CYCLE.length]
            setDensity(next)
          }}
          title={`布局密度：${DENSITY_LABEL[density]}（点击切换）`}
          className="flex items-center gap-1.5 rounded-lg border border-strong bg-surface px-1.5 sm:px-2.5 py-1.5 text-xs text-muted transition-colors hover:border-strong hover:text-secondary"
        >
          <Columns2 className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">{DENSITY_LABEL[density]}</span>
        </button>
        <button
          onClick={() => setThinkingCollapsed(!thinkingCollapsed)}
          title={thinkingCollapsed ? '展开思考过程' : '折叠思考过程'}
          className={`flex items-center gap-1.5 rounded-lg border px-1.5 sm:px-2.5 py-1.5 text-xs transition-colors ${
            thinkingCollapsed
              ? 'border-strong bg-surface text-muted hover:border-strong hover:text-secondary'
              : 'border-violet-500/50 bg-thinking-surface text-thinking-fg hover:bg-violet-500/20'
          }`}
        >
          <Sparkles className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">{thinkingCollapsed ? '思考折叠' : '思考展开'}</span>
        </button>
        <button
          onClick={toggleTheme}
          title={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
          className="flex items-center gap-1.5 rounded-lg border border-strong bg-surface px-1.5 sm:px-2.5 py-1.5 text-xs text-muted transition-colors hover:border-strong hover:text-secondary"
        >
          {theme === 'dark' ? (
            <Sun className="h-3.5 w-3.5" />
          ) : (
            <Moon className="h-3.5 w-3.5" />
          )}
        </button>
        <button
          onClick={toggleRightPanel}
          title={rightPanelVisible ? '隐藏右侧面板' : '显示右侧面板'}
          className="flex items-center gap-1.5 rounded-lg border border-strong bg-surface px-1.5 sm:px-2.5 py-1.5 text-xs text-muted hover:border-strong hover:text-secondary"
        >
          {rightPanelVisible ? (
            <PanelRightClose className="h-3.5 w-3.5" />
          ) : (
            <PanelRight className="h-3.5 w-3.5" />
          )}
        </button>
        <button
          onClick={() => setSearchOpen(true)}
          aria-label="搜索消息"
          className="flex items-center gap-2 rounded-lg border border-strong bg-surface px-1.5 sm:px-3 py-1.5 text-xs text-muted hover:border-strong hover:text-secondary"
        >
          <Search className="h-3 w-3" />
          <span className="hidden sm:inline">搜索消息</span>
          <kbd className="ml-2 hidden sm:inline rounded border border-strong px-1.5 py-0.5 text-[10px] text-muted">
            <Command className="inline h-2.5 w-2.5" /> K
          </kbd>
        </button>
      </div>
    </header>
  )
}