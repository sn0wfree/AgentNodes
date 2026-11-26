import { useState, useRef, useEffect, useMemo } from 'react'
import {
  Plus, Search, Star, Archive, Trash2, Pencil, X, MessageSquare, Tag,
} from 'lucide-react'
import { useSessionStore, type Session } from '../../stores/session'
import { useLayoutStore } from '../../stores/layout'
import { ConfirmDialog } from '../common/ConfirmDialog'
import { groupSessions, SESSION_GROUP_LABELS } from '../../utils/sessionGroups'

type Filter = 'all' | 'starred' | 'archived'

function formatTime(ts: number): string {
  const now = Date.now() / 1000
  const diff = now - ts
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`
  if (diff < 604800) return `${Math.floor(diff / 86400)}天前`
  return new Date(ts * 1000).toLocaleDateString('zh-CN')
}

export function SessionSidebar() {
  const sessions = useSessionStore((s) => s.sessions)
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const switchSession = useSessionStore((s) => s.switchSession)
  const createNewSession = useSessionStore((s) => s.createNewSession)
  const updateSessionMeta = useSessionStore((s) => s.updateSessionMeta)
  const deleteSession = useSessionStore((s) => s.deleteSession)
  const setSidebarOpen = useLayoutStore((s) => s.setSidebarOpen)
  const requestRename = useLayoutStore((s) => s.requestRename)

  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [taggingId, setTaggingId] = useState<string | null>(null)
  const [tagInput, setTagInput] = useState('')
  const [pendingDelete, setPendingDelete] = useState<{ id: string; title: string } | null>(null)
  const tagRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (taggingId && tagRef.current) {
      tagRef.current.focus()
    }
  }, [taggingId])

  const filtered = useMemo(() => {
    let list = sessions
    if (filter === 'starred') list = list.filter((s) => s.starred && !s.archived)
    else if (filter === 'archived') list = list.filter((s) => s.archived)
    else list = list.filter((s) => !s.archived && !s.id.startsWith('dag:'))

    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter((s) => s.title.toLowerCase().includes(q))
    }

    return [...list].sort((a, b) => b.updated_at - a.updated_at)
  }, [sessions, filter, search])

  const grouped = useMemo(() => groupSessions(filtered), [filtered])

  const handleNew = async () => {
    await createNewSession('新会话')
    if (useLayoutStore.getState().narrowViewport) setSidebarOpen(false)
  }

  const handleSelect = (id: string) => {
    if (id !== currentSessionId) {
      void switchSession(id)
    }
    if (useLayoutStore.getState().narrowViewport) setSidebarOpen(false)
  }

  // 重命名入口合并：切换到目标会话 + 置位信号，由 TopBar 统一进入编辑态。
  const handleRequestRename = (sess: Session) => {
    if (sess.id !== currentSessionId) void switchSession(sess.id)
    requestRename(sess.id)
  }

  const handleToggleStar = async (id: string) => {
    const sess = sessions.find((s) => s.id === id)
    if (sess) await updateSessionMeta(id, { starred: !sess.starred })
  }

  const handleToggleArchive = async (id: string) => {
    const sess = sessions.find((s) => s.id === id)
    if (sess) await updateSessionMeta(id, { archived: !sess.archived })
  }

  const handleStartTag = (sess: Session) => {
    setTaggingId(sess.id)
    setTagInput(sess.tags.join(', '))
  }

  const handleCommitTags = async () => {
    if (!taggingId) return
    const tags = tagInput
      .split(',')
      .map((t) => t.trim().slice(0, 32))
      .filter(Boolean)
    try {
      await updateSessionMeta(taggingId, { tags })
    } catch (err) {
      console.error('tag failed', err)
    }
    setTaggingId(null)
  }

  const handleDelete = (sess: Session) => {
    setPendingDelete({ id: sess.id, title: sess.title })
  }

  const confirmDelete = async () => {
    if (!pendingDelete) return
    await deleteSession(pendingDelete.id)
    setPendingDelete(null)
  }

  return (
    <div className="flex h-full w-64 flex-col border-r border-subtle bg-surface-raised">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-subtle px-3 py-2">
        <span className="text-sm font-medium text-secondary">会话</span>
        <div className="flex gap-1">
          <button
            onClick={() => setSidebarOpen(false)}
            className="rounded p-1 text-muted hover:bg-surface-raised hover:text-foreground"
            title="收起侧边栏"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="px-3 pt-3">
        <button
          onClick={handleNew}
          className="ua-primary-action flex w-full items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors"
          title="新建会话"
        >
          <Plus className="h-4 w-4" />
          新建会话
        </button>
      </div>

      {/* Search */}
      <div className="px-3 py-2">
        <div className="relative rounded-md border border-strong bg-surface-raised">
          <Search className="absolute left-2 top-1/2 z-10 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索会话..."
            className="relative z-10 w-full rounded-md bg-transparent py-1.5 pl-8 pr-2 text-sm text-foreground placeholder-slate-500 outline-none focus:border-primary-500"
          />
        </div>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-1 px-3 pb-2">
        {(['all', 'starred', 'archived'] as Filter[]).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`rounded px-2 py-0.5 text-xs transition-colors ${
              filter === f
                ? 'bg-surface-raised text-foreground'
                : 'text-muted hover:bg-surface-raised hover:text-foreground'
            }`}
          >
            {f === 'all' ? '全部' : f === 'starred' ? '星标' : '归档'}
          </button>
        ))}
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto px-2">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center py-8 text-muted">
            <MessageSquare className="mb-2 h-8 w-8" />
            {search.trim() ? (
              <>
                <span className="text-sm">没有匹配「{search.trim()}」的会话</span>
                <button
                  onClick={() => setSearch('')}
                  className="mt-2 rounded px-2 py-1 text-xs text-muted hover:bg-surface-raised hover:text-foreground"
                >
                  清除搜索
                </button>
              </>
            ) : filter !== 'all' ? (
              <>
                <span className="text-sm">{filter === 'starred' ? '暂无星标会话' : '暂无归档会话'}</span>
                <button
                  onClick={() => setFilter('all')}
                  className="mt-2 rounded px-2 py-1 text-xs text-muted hover:bg-surface-raised hover:text-foreground"
                >
                  查看全部
                </button>
              </>
            ) : (
              <span className="text-sm">暂无会话</span>
            )}
          </div>
        ) : (
          SESSION_GROUP_LABELS.map(({ key, label }) => {
            const items = grouped[key]
            if (items.length === 0) return null
            return (
              <div key={key} className="mb-1">
                <div className="px-2 pb-1 pt-2.5 text-[10px] font-medium uppercase tracking-wider text-muted">
                  {label}
                </div>
                {items.map((sess) => (
                  <SessionRow
                    key={sess.id}
                    sess={sess}
                    isActive={currentSessionId === sess.id}
                    tagging={taggingId === sess.id}
                    tagInput={tagInput}
                    tagRef={tagRef}
                    onSelect={() => handleSelect(sess.id)}
                    onTagChange={(v) => setTagInput(v)}
                    onTagCommit={handleCommitTags}
                    onTagCancel={() => setTaggingId(null)}
                    onToggleStar={() => void handleToggleStar(sess.id)}
                    onRequestRename={() => handleRequestRename(sess)}
                    onStartTag={() => handleStartTag(sess)}
                    onToggleArchive={() => void handleToggleArchive(sess.id)}
                    onDelete={() => handleDelete(sess)}
                  />
                ))}
              </div>
            )
          })
        )}
      </div>

      {/* Delete confirmation */}
      <ConfirmDialog
        open={!!pendingDelete}
        onOpenChange={(open) => { if (!open) setPendingDelete(null) }}
        title="删除会话"
        description={`确定要删除「${pendingDelete?.title ?? ''}」吗？此操作不可撤销。`}
        confirmLabel="删除"
        variant="danger"
        onConfirm={confirmDelete}
      />
    </div>
  )
}

interface SessionRowProps {
  sess: Session
  isActive: boolean
  tagging: boolean
  tagInput: string
  tagRef: React.RefObject<HTMLInputElement | null>
  onSelect: () => void
  onTagChange: (v: string) => void
  onTagCommit: () => void
  onTagCancel: () => void
  onToggleStar: () => void
  onRequestRename: () => void
  onStartTag: () => void
  onToggleArchive: () => void
  onDelete: () => void
}

function SessionRow({
  sess,
  isActive,
  tagging,
  tagInput,
  tagRef,
  onSelect,
  onTagChange,
  onTagCommit,
  onTagCancel,
  onToggleStar,
  onRequestRename,
  onStartTag,
  onToggleArchive,
  onDelete,
}: SessionRowProps) {
  return (
    <div
      onClick={onSelect}
      className={`group mb-1 cursor-pointer rounded-md px-2 py-1.5 transition-colors ${
        isActive
          ? 'bg-surface-raised text-foreground'
          : 'text-secondary hover:bg-surface-raised'
      }`}
    >
      {tagging ? (
        <input
          ref={tagRef}
          value={tagInput}
          onChange={(e) => onTagChange(e.target.value)}
          onBlur={onTagCommit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') onTagCommit()
            if (e.key === 'Escape') onTagCancel()
          }}
          onClick={(e) => e.stopPropagation()}
          placeholder="tag1, tag2"
          className="w-full rounded border border-primary-500 bg-surface-raised px-1 py-0.5 text-sm text-foreground outline-none"
        />
      ) : (
        <>
          <div className="flex items-center gap-1.5">
            {sess.starred && (
              <Star className="h-3 w-3 flex-shrink-0 fill-yellow-400 text-yellow-400" />
            )}
            <span className="flex-1 truncate text-sm">{sess.title}</span>
            {/* B4 D2: message_count was removed from this row. It is
                maintained only by the legacy persist path, so on every
                session written through the projector it reads 0 (measured:
                93/93 sessions) -- a permanently wrong number. The accurate
                value is GET /sessions/{id}/messages -> total, which this
                list view cannot afford (it would be one request per row).
                The per-session count still shows in TokenCard, where the
                message list is already loaded. */}
          </div>
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted">
              {formatTime(sess.updated_at)}
            </span>
            {/* Action buttons (visible on hover) */}
            <div className="hidden gap-0.5 group-hover:flex">
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onToggleStar()
                }}
                className="rounded p-0.5 text-muted hover:text-yellow-400"
                title={sess.starred ? '取消星标' : '星标'}
              >
                <Star className={`h-3 w-3 ${sess.starred ? 'fill-yellow-400' : ''}`} />
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onRequestRename()
                }}
                className="rounded p-0.5 text-muted hover:text-foreground"
                title="重命名"
              >
                <Pencil className="h-3 w-3" />
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onStartTag()
                }}
                className="rounded p-0.5 text-muted hover:text-foreground"
                title="编辑标签"
              >
                <Tag className="h-3 w-3" />
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onToggleArchive()
                }}
                className="rounded p-0.5 text-muted hover:text-foreground"
                title={sess.archived ? '取消归档' : '归档'}
              >
                <Archive className="h-3 w-3" />
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onDelete()
                }}
                className="rounded p-0.5 text-muted hover:text-danger-fg"
                title="删除"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
