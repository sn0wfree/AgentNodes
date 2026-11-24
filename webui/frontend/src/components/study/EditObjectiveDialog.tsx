import { useEffect, useState } from 'react'
import { Edit3, Loader2, X } from 'lucide-react'
import { api } from '../../api/client'

interface EditObjectiveDialogProps {
  studyId: string
  currentObjective: string
  goalId: string | null
  open: boolean
  onClose: () => void
  onSuccess?: (newObjective: string) => void
}

export function EditObjectiveDialog({
  studyId,
  currentObjective,
  goalId,
  open,
  onClose,
  onSuccess,
}: EditObjectiveDialogProps) {
  const [text, setText] = useState(currentObjective)
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (open) {
      setText(currentObjective)
      setReason('')
      setError('')
    }
  }, [open, currentObjective])

  if (!open) return null

  const trimmed = text.trim()
  const lengthOk = trimmed.length >= 10 && trimmed.length <= 2000
  const changed = trimmed !== currentObjective.trim()
  const canSubmit = lengthOk && changed && !!goalId && !submitting

  const handleSubmit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    setError('')
    try {
      await api.study.replaceObjective(
        studyId,
        trimmed,
        goalId,
        reason.trim() || undefined,
      )
      onSuccess?.(trimmed)
      onClose()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="修改研究目标"
      className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/70 px-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex w-full max-w-xl flex-col gap-4 rounded-2xl border border-slate-700 bg-slate-900 p-5 shadow-elevated"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-100">
            <Edit3 className="h-4 w-4 text-primary-400" />
            修改研究目标
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-slate-500 transition-colors hover:bg-slate-800 hover:text-slate-300"
            title="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Current objective (read-only) */}
        <div>
          <label className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-slate-500">
            当前目标
          </label>
          <div className="rounded-lg border border-slate-800 bg-slate-950/50 px-2.5 py-2 text-xs leading-relaxed text-slate-400">
            {currentObjective}
          </div>
        </div>

        {/* New objective */}
        <div>
          <label className="mb-1 flex items-center justify-between text-[10px] font-medium uppercase tracking-wider text-slate-500">
            <span>新目标</span>
            <span
              className={`font-mono normal-case ${
                lengthOk ? 'text-slate-600' : 'text-danger-fg'
              }`}
            >
              {trimmed.length} / 2000
            </span>
          </label>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={4}
            placeholder="例：低估值反转因子选股 + 限制 top_n ≤ 30"
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 text-xs leading-relaxed text-slate-200 outline-none transition-shadow focus:border-primary-500 focus:ring-2 focus:ring-primary-500/40"
          />
          {!lengthOk && trimmed.length > 0 && (
            <p className="mt-1 text-[10px] text-danger-fg">
              目标长度需在 10–2000 字之间
            </p>
          )}
        </div>

        {/* Reason */}
        <div>
          <label className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-slate-500">
            修改原因（可选 · 会记入审计日志）
          </label>
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={512}
            placeholder="例：最近回测显示动量失效，反转更稳"
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 outline-none transition-shadow focus:border-primary-500 focus:ring-2 focus:ring-primary-500/40"
          />
        </div>

        {/* Hint */}
        <div className="rounded-lg border border-warning/30 bg-warning-fill px-3 py-2 text-[10px] leading-relaxed text-warning-fg">
          ⚠ 新目标将从<strong>下一轮</strong>生效，历史目标会自动保留可在标题旁 ⓘ 查看。
        </div>

        {/* Error */}
        {error && (
          <div className="rounded-lg border border-rose-800 bg-rose-950/50 px-3 py-2 text-[11px] text-rose-300">
            {error}
          </div>
        )}

        {!goalId && (
          <div className="rounded-lg border border-warning/30 bg-warning-fill px-3 py-2 text-[10px] leading-relaxed text-warning-fg">
            ⚠ 此研究尚未关联 goal ledger，无法执行目标修改。
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-xs text-slate-300 transition-colors hover:bg-slate-800 disabled:opacity-50"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void handleSubmit()}
            disabled={!canSubmit}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs text-white transition-all hover:bg-primary-500 active:scale-95 disabled:opacity-40"
          >
            {submitting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Edit3 className="h-3.5 w-3.5" />
            )}
            提交修改
          </button>
        </div>
      </div>
    </div>
  )
}