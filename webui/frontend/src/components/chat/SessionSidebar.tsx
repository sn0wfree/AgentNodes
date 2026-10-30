import { useState, useRef, useEffect, useMemo } from 'react'
import {
  Plus, Search, Star, Archive, Trash2, Edit3, X, MessageSquare, Tag,
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

  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const [taggingId, setTaggingId] = useState<string | null>(null)
  const [tagInput, setTagInput] = useState('')
  const [pendingDelete, setPendingDelete] = useState<{ id: string; title: string } | null>(null)
  const editRef = useRef<HTMLInputElement>(null)
  const tagRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editingId && editRef.current) {
      editRef.current.focus()
      editRef.current.select()
    }
  }, [editingId])

  useEffect(() => {
    if (taggingId && tagRef.current) {
      tagRef.current.focus()
    }
  }, [taggingId])

  // Test/debug session keywords to filter out
  const TEST_KEYWORDS = ['test', 'verify', 'debug', 'diag', 'check', 'probe', 'quick']

  const isTestSession = (title: string) => {
    const lower = title.toLowerCase()
    return TEST_KEYWORDS.some((kw) => lower.includes(kw))
  }

  const filtered = useMemo(() => {
    let list = sessions
    if (filter === 'starred') list = list.filter((s) => s.starred && !s.archived)
    else if (filter === 'archived') list = list.filter((s) => s.archived)
    else list = list.filter((s) => !s.archived && !isTestSession(s.title) && !s.id.startsWith('dag:'))

    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter((s) => s.title.toLowerCase().includes(q))
    }

    return [...list].sort((a, b) => b.updated_at - a.updated_at)
  }, [sessions, filter, search])

  const grouped = useMemo(() => groupSessions(filtered), [filtered])

  const handleNew = async () => {
    await createNewSession('新会话')
  }

  const handleSelect = (id: string) => {
    if (id !== currentSessionId) {
      void switchSession(id)
    }
  }

  const handleStartEdit = (sess: Session) => {
    setEditingId(sess.id)
    setEditValue(sess.title)
  }

  const handleCommitEdit = async () => {
    if (!editingId) return
    const trimmed = editValue.trim()
    if (trimmed && trimmed !== sessions.find((s) => s.id === editingId)?.title) {
      await updateSessionMeta(editingId, { title: trimmed })
    }
    setEditingId(null)
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
    <div className="flex h-full w-64 flex-col border-r border-slate-800 bg-slate-900/80">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-800 px-3 py-2">
        <span className="text-sm font-medium text-slate-300">会话</span>
        <div className="flex gap-1">
          <button
            onClick={handleNew}
            className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
            title="新建会话"
          >
            <Plus className="h-4 w-4" />
          </button>
          <button
            onClick={() => setSidebarOpen(false)}
            className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
            title="收起侧边栏"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Search */}
      <div className="px-3 py-2">
        <div className="glow-border relative rounded-md border border-slate-700 bg-slate-800">
          <Search className="absolute left-2 top-1/2 z-10 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索会话..."
            className="relative z-10 w-full rounded-md bg-transparent py-1.5 pl-8 pr-2 text-sm text-slate-200 placeholder-slate-500 outline-none focus:border-primary-500"
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
                ? 'bg-primary-600/20 text-primary-400'
                : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
            }`}
          >
            {f === 'all' ? '全部' : f === 'starred' ? '星标' : '归档'}
          </button>
        ))}
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto px-2">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center py-8 text-slate-500">
            <MessageSquare className="mb-2 h-8 w-8" />
            <span className="text-sm">暂无会话</span>
          </div>
        ) : (
          SESSION_GROUP_LABELS.map(({ key, label }) => {
            const items = grouped[key]
            if (items.length === 0) return null
            return (
              <div key={key} className="mb-1">
                <div className="px-2 pb-1 pt-2.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
                  {label}
                </div>
                {items.map((sess) => (
                  <SessionRow
                    key={sess.id}
                    sess={sess}
                    isActive={currentSessionId === sess.id}
                    editing={editingId === sess.id}
                    tagging={taggingId === sess.id}
                    editValue={editValue}
                    tagInput={tagInput}
                    editRef={editRef}
                    tagRef={tagRef}
                    onSelect={() => handleSelect(sess.id)}
                    onEditChange={(v) => setEditValue(v)}
                    onEditCommit={handleCommitEdit}
                    onEditCancel={() => setEditingId(null)}
                    onTagChange={(v) => setTagInput(v)}
                    onTagCommit={handleCommitTags}
                    onTagCancel={() => setTaggingId(null)}
                    onToggleStar={() => void handleToggleStar(sess.id)}
                    onStartEdit={() => handleStartEdit(sess)}
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
  editing: boolean
  tagging: boolean
  editValue: string
  tagInput: string
  editRef: React.RefObject<HTMLInputElement | null>
  tagRef: React.RefObject<HTMLInputElement | null>
  onSelect: () => void
  onEditChange: (v: string) => void
  onEditCommit: () => void
  onEditCancel: () => void
  onTagChange: (v: string) => void
  onTagCommit: () => void
  onTagCancel: () => void
  onToggleStar: () => void
  onStartEdit: () => void
  onStartTag: () => void
  onToggleArchive: () => void
  onDelete: () => void
}

function SessionRow({
  sess,
  isActive,
  editing,
  tagging,
  editValue,
  tagInput,
  editRef,
  tagRef,
  onSelect,
  onEditChange,
  onEditCommit,
  onEditCancel,
  onTagChange,
  onTagCommit,
  onTagCancel,
  onToggleStar,
  onStartEdit,
  onStartTag,
  onToggleArchive,
  onDelete,
}: SessionRowProps) {
  return (
    <div
      onClick={onSelect}
      className={`group mb-1 cursor-pointer rounded-md px-2 py-1.5 transition-colors ${
        isActive
          ? 'bg-primary-600/20 text-primary-300'
          : 'text-slate-300 hover:bg-slate-800'
      }`}
    >
      {editing ? (
        <input
          ref={editRef}
          value={editValue}
          onChange={(e) => onEditChange(e.target.value)}
          onBlur={onEditCommit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') onEditCommit()
            if (e.key === 'Escape') onEditCancel()
          }}
          className="w-full rounded border border-primary-500 bg-slate-800 px-1 py-0.5 text-sm text-slate-200 outline-none"
          onClick={(e) => e.stopPropagation()}
        />
      ) : tagging ? (
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
          className="w-full rounded border border-primary-500 bg-slate-800 px-1 py-0.5 text-sm text-slate-200 outline-none"
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
            <span className="text-xs text-slate-500">
              {formatTime(sess.updated_at)}
            </span>
            {/* Action buttons (visible on hover) */}
            <div className="hidden gap-0.5 group-hover:flex">
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onToggleStar()
                }}
                className="rounded p-0.5 text-slate-400 hover:text-yellow-400"
                title={sess.starred ? '取消星标' : '星标'}
              >
                <Star className={`h-3 w-3 ${sess.starred ? 'fill-yellow-400' : ''}`} />
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onStartEdit()
                }}
                className="rounded p-0.5 text-slate-400 hover:text-slate-200"
                title="重命名"
              >
                <Edit3 className="h-3 w-3" />
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onStartTag()
                }}
                className="rounded p-0.5 text-slate-400 hover:text-slate-200"
                title="编辑标签"
              >
                <Tag className="h-3 w-3" />
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onToggleArchive()
                }}
                className="rounded p-0.5 text-slate-400 hover:text-slate-200"
                title={sess.archived ? '取消归档' : '归档'}
              >
                <Archive className="h-3 w-3" />
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onDelete()
                }}
                className="rounded p-0.5 text-slate-400 hover:text-red-400"
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
