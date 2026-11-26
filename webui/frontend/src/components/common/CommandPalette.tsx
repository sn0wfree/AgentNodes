import { useState, useEffect, useCallback, useMemo } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { useCommandPaletteStore } from '../../stores/commandPalette'
import {
  Search, MessageSquare, Workflow, Plus,
  Settings, Eye, EyeOff, RefreshCw, ArrowRight, Layers, Activity,
} from 'lucide-react'
import { useLayoutStore } from '../../stores/layout'
import { useSessionStore } from '../../stores/session'
import { useChatStore } from '../../stores/chat'
import { useNavigate } from 'react-router-dom'

interface Command {
  id: string
  label: string
  description?: string
  category: 'navigation' | 'action' | 'view' | 'session'
  icon: any
  shortcut?: string
  action: () => void
  keywords?: string[]
}

function fuzzyMatch(query: string, text: string): number {
  if (!query) return 1
  const q = query.toLowerCase()
  const t = text.toLowerCase()
  if (t.includes(q)) return 1 + (t.startsWith(q) ? 0.5 : 0)
  return 0
}

export function CommandPalette() {
  const open = useCommandPaletteStore((s) => s.open)
  const setOpen = useCommandPaletteStore((s) => s.setOpen)
  const navigate = useNavigate()
  const toggleRightPanel = useLayoutStore((s) => s.toggleRightPanel)
  const setWorkMode = useLayoutStore((s) => s.setWorkMode)
  const toggleSidebar = useLayoutStore((s) => s.toggleSidebar)
  const createNewSession = useSessionStore((s) => s.createNewSession)
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const setSearchOpen = useSessionStore((s) => s.setSearchOpen)
  const loadMessages = useChatStore((s) => s.loadMessages)

  const [query, setQuery] = useState('')
  const [selectedIdx, setSelectedIdx] = useState(0)

  const commands: Command[] = useMemo(() => [
    // View
    {
      id: 'view-trace',
      label: '打开 Trace',
      description: '历史会话的事件时间线 · 工具调用 · 推理轨迹回放',
      category: 'view',
      icon: Workflow,
      shortcut: '⌘W',
      action: () => { navigate('/trace'); setOpen(false) },
      keywords: ['trace', '轨迹', '历史', '回放', '事件', '工具调用', 'reasoning'],
    },
    {
      id: 'view-toggle',
      label: '切换右主区可见性',
      description: '显示/隐藏右主区',
      category: 'view',
      icon: Eye,
      action: () => { toggleRightPanel(); setOpen(false) },
      keywords: ['panel', '区'],
    },
    {
      id: 'mode-chat',
      label: '切换到纯聊天模式',
      description: '折叠右主区，仅显示聊天',
      category: 'view',
      icon: MessageSquare,
      action: () => { setWorkMode('chat'); setOpen(false) },
      keywords: ['chat', '聊天'],
    },
    {
      id: 'mode-monitor',
      label: '切换到监控模式',
      description: '双主区模式（默认）',
      category: 'view',
      icon: Layers,
      action: () => { setWorkMode('monitor'); setOpen(false) },
      keywords: ['monitor', '监控'],
    },
    {
      id: 'mode-focus',
      label: '切换到专注模式',
      description: '折叠左主区，仅显示右主区',
      category: 'view',
      icon: EyeOff,
      action: () => { setWorkMode('focus'); setOpen(false) },
      keywords: ['focus', '专注'],
    },

    // Action
    {
      id: 'action-new-session',
      label: '新建会话',
      description: '创建新会话并开始聊天',
      category: 'action',
      icon: Plus,
      shortcut: '⌘T',
      action: () => {
        setOpen(false)
        void createNewSession('新会话')
      },
      keywords: ['new', 'session', '新建'],
    },
    {
      id: 'action-refresh',
      label: '刷新当前会话',
      description: '重新加载当前会话的消息',
      category: 'action',
      icon: RefreshCw,
      action: () => {
        setOpen(false)
        if (currentSessionId) void loadMessages(currentSessionId)
      },
      keywords: ['refresh', 'reload'],
    },
    {
      id: 'action-search-messages',
      label: '搜索消息',
      description: 'FTS5 全文搜索所有会话',
      category: 'action',
      icon: Search,
      shortcut: '⌘K',
      action: () => {
        setOpen(false)
        setSearchOpen(true)
      },
      keywords: ['search', 'find', '搜索', '查找'],
    },

    // Navigation
    {
      id: 'nav-chat',
      label: '打开 Chat',
      description: '进入聊天工作区（会话 / 研究对话）',
      category: 'navigation',
      icon: MessageSquare,
      shortcut: '⌘T',
      action: () => { navigate('/chat'); toggleSidebar(); setOpen(false) },
      keywords: ['chat', '聊天', '会话', '对话'],
    },
    {
      id: 'nav-home',
      label: '监控首页',
      description: '聚合总览：KPI、活跃研究、最近运行',
      category: 'navigation',
      icon: Activity,
      action: () => { navigate('/'); setOpen(false) },
      keywords: ['home', '首页', '监控', 'dashboard', '总览'],
    },
    {
      id: 'nav-settings',
      label: '设置',
      description: '打开设置页面',
      category: 'navigation',
      icon: Settings,
      action: () => { setOpen(false); navigate('/account') },
      keywords: ['settings', '配置'],
    },
  ], [toggleRightPanel, setWorkMode, navigate, setOpen, createNewSession, currentSessionId, setSearchOpen, loadMessages, navigate])

  // Filter by query
  const filtered = useMemo(() => {
    if (!query.trim()) return commands
    return commands
      .map((cmd) => {
        let score = fuzzyMatch(query, cmd.label)
        if (cmd.description) score += fuzzyMatch(query, cmd.description)
        if (cmd.keywords) {
          for (const kw of cmd.keywords) {
            score += fuzzyMatch(query, kw)
          }
        }
        return { cmd, score }
      })
      .filter((r) => r.score > 0)
      .sort((a, b) => b.score - a.score)
      .map((r) => r.cmd)
  }, [commands, query])

  useEffect(() => {
    setSelectedIdx(0)
  }, [query, open])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIdx((i) => (i + 1) % Math.max(filtered.length, 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIdx((i) => (i - 1 + filtered.length) % Math.max(filtered.length, 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const cmd = filtered[selectedIdx]
      if (cmd) cmd.action()
    }
  }, [filtered, selectedIdx])

  // Group by category
  const grouped = useMemo(() => {
    const groups: Record<string, Command[]> = {}
    filtered.forEach((cmd) => {
      if (!groups[cmd.category]) groups[cmd.category] = []
      groups[cmd.category].push(cmd)
    })
    return groups
  }, [filtered])

  const CATEGORY_LABEL = {
    view: '视图',
    action: '操作',
    navigation: '导航',
    session: '会话',
  }

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" />
        <Dialog.Content
          className="fixed left-1/2 top-[20%] z-50 w-full max-w-xl -translate-x-1/2 rounded-xl bg-background border border-strong shadow-2xl"
        >
          <Dialog.Title className="sr-only">Command Palette</Dialog.Title>
          <Dialog.Description className="sr-only">
            快速执行命令、切换视图、导航
          </Dialog.Description>

          {/* Search input */}
          <div className="flex items-center gap-3 border-b border-strong px-4 py-3">
            <Search className="h-4 w-4 text-muted" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="搜索命令、视图、操作..."
              className="flex-1 bg-transparent text-sm text-foreground placeholder-slate-500 outline-none"
            />
            <kbd className="rounded border border-strong px-1.5 py-0.5 text-[10px] text-muted">
              ESC
            </kbd>
          </div>

          {/* Results */}
          <div className="max-h-80 overflow-y-auto p-2">
            {filtered.length === 0 ? (
              <div className="py-8 text-center text-sm text-muted">
                未找到匹配命令
              </div>
            ) : (
              Object.entries(grouped).map(([category, cmds]) => (
                <div key={category} className="mb-1">
                  <div className="px-2 py-1 text-[10px] uppercase tracking-wider text-muted">
                    {CATEGORY_LABEL[category as keyof typeof CATEGORY_LABEL] || category}
                  </div>
                  {cmds.map((cmd) => {
                    const globalIdx = filtered.indexOf(cmd)
                    const isSelected = globalIdx === selectedIdx
                    const Icon = cmd.icon
                    return (
                      <button
                        key={cmd.id}
                        onClick={() => cmd.action()}
                        onMouseEnter={() => setSelectedIdx(globalIdx)}
                        className={`flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left text-sm transition-colors
                          ${isSelected ? 'bg-primary-600/20 text-primary-100' : 'text-secondary hover:bg-surface-raised'}
                        `}
                      >
                        {Icon && <Icon className="h-4 w-4 flex-shrink-0" />}
                        <div className="flex-1 min-w-0">
                          <div className="truncate">{cmd.label}</div>
                          {cmd.description && (
                            <div className="truncate text-xs text-muted">
                              {cmd.description}
                            </div>
                          )}
                        </div>
                        {cmd.shortcut && (
                          <kbd className="rounded border border-strong px-1.5 py-0.5 text-[10px] text-muted">
                            {cmd.shortcut}
                          </kbd>
                        )}
                        {isSelected && (
                          <ArrowRight className="h-3 w-3 text-primary-400" />
                        )}
                      </button>
                    )
                  })}
                </div>
              ))
            )}
          </div>

          {/* Footer hint */}
          <div className="flex items-center justify-between border-t border-strong px-4 py-2 text-[10px] text-muted">
            <div className="flex items-center gap-3">
              <span className="flex items-center gap-1">
                <kbd className="rounded border border-strong px-1 py-0.5">↑</kbd>
                <kbd className="rounded border border-strong px-1 py-0.5">↓</kbd>
                导航
              </span>
              <span className="flex items-center gap-1">
                <kbd className="rounded border border-strong px-1 py-0.5">↵</kbd>
                执行
              </span>
            </div>
            <span>{filtered.length} 个命令</span>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}