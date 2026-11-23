import { useEffect, useRef, useState } from 'react'
import { Archive, ArchiveRestore, MoreVertical, Pause, Play, X } from 'lucide-react'
import { api, type StudyActionItem, type StudySummary, type StudySummaryResponse } from '../../api/client'

type StudyLike = Pick<StudySummary, 'study_id' | 'execution_status'> &
  Partial<StudySummaryResponse>

interface StudyActionMenuProps {
  study: StudyLike
  onAction: (action: 'pause' | 'continue' | 'cancel' | 'archive' | 'unarchive') => void
  onRefresh?: () => void
}

const ICONS: Record<string, typeof Pause> = {
  pause: Pause,
  continue: Play,
  cancel: X,
  archive: Archive,
  unarchive: ArchiveRestore,
}

const ICON_CLS: Record<string, string> = {
  pause: 'text-warning-fg',
  continue: 'text-emerald-400',
  cancel: 'text-rose-400',
  archive: 'text-slate-400',
  unarchive: 'text-sky-400',
}

export function StudyActionMenu({ study, onAction, onRefresh }: StudyActionMenuProps) {
  const [open, setOpen] = useState(false)
  const [actions, setActions] = useState<StudyActionItem[]>([])
  const [loading, setLoading] = useState(false)
  const containerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    void (async () => {
      try {
        const r = await api.study.availableActions(study.study_id)
        if (!cancelled) setActions(r.actions)
      } catch {
        if (!cancelled) setActions([])
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [open, study.study_id])

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const handleClick = async (name: string) => {
    setOpen(false)
    if (name === 'cancel' && !window.confirm('确定中止此研究？中止后可从 Round 1 重新开始。')) {
      return
    }
    if (name === 'archive' && !window.confirm('确定归档此研究？')) {
      return
    }
    if (
      name === 'pause' ||
      name === 'continue' ||
      name === 'cancel' ||
      name === 'archive' ||
      name === 'unarchive'
    ) {
      await onAction(name)
      onRefresh?.()
    }
    // Other action names (replace_objective / directive / redo…) need an
    // argument UI this menu doesn't have — they are filtered out below
    // instead of rendering dead entries (中-1).
  }

  return (
    <div ref={containerRef} className="relative inline-block">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          setOpen((v) => !v)
        }}
        title="操作"
        aria-label="操作"
        className="inline-flex cursor-pointer items-center justify-center rounded-lg p-1 text-slate-500 transition-colors hover:bg-slate-800 hover:text-slate-300 active:scale-95"
      >
        <MoreVertical className="h-3.5 w-3.5" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-30 mt-1 min-w-[10rem] rounded-lg border border-slate-700 bg-slate-900/95 py-1 shadow-elevated backdrop-blur"
        >
          {loading ? (
            <div className="px-3 py-2 text-[10px] text-slate-500">加载中...</div>
          ) : actions.length === 0 ? (
            <div className="px-3 py-2 text-[10px] text-slate-500">暂无可用操作</div>
          ) : (
            actions
              // 中-1: actions needing an argument UI (replace_objective's
              // objective input, directive's text) live on the detail
              // page — rendering them here produced dead menu items.
              .filter((a) => ICONS[a.name] !== undefined)
              .map((a) => {
                const Icon = ICONS[a.name]
                return (
                  <button
                    key={a.name}
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      handleClick(a.name)
                    }}
                    role="menuitem"
                    className={`flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-[11px] transition-colors hover:bg-slate-800 ${
                      a.destructive ? 'text-rose-300 hover:text-rose-200' : 'text-slate-200 hover:text-slate-50'
                    }`}
                  >
                    {Icon && <Icon className={`h-3.5 w-3.5 ${ICON_CLS[a.name] ?? ''}`} />}
                    <span>{a.label}</span>
                  </button>
                )
              })
          )}
        </div>
      )}
    </div>
  )
}