/**
 * ContinueDialog — unified modal for continuing a study.
 *
 * Replaces the separate RESUME_INTERRUPTED / RETRY actions with a single
 * CONTINUE action. The user picks from three modes:
 *   - resume: continue at current round (default for INTERRUPTED/PAUSED)
 *   - restart: restart from round 1 (default for ERROR/CANCELLED/COMPLETE)
 *   - from_round: start from a specific round number
 */
import { useState, useEffect } from 'react'
import { Play, RotateCcw, X } from 'lucide-react'
import type { StudySummaryResponse } from '../../api/client'

type ContinueMode = 'resume' | 'restart'

interface ContinueDialogProps {
  open: boolean
  summary: StudySummaryResponse | null
  onClose: () => void
  onContinue: (mode: ContinueMode, fromRound?: number) => void
}

const STATUS_DEFAULT_MODE: Record<string, ContinueMode> = {
  interrupted: 'resume',
  paused: 'resume',
  error: 'restart',
  budget_limited: 'restart',
  early_stopped: 'restart',
  needs_refresh: 'restart',
  cancelled: 'restart',
  complete: 'restart',
}

const STATUS_LABELS: Record<string, string> = {
  interrupted: '已中断',
  paused: '已暂停',
  error: '错误',
  budget_limited: '预算受限',
  early_stopped: '提前停止',
  needs_refresh: '需刷新',
  cancelled: '已取消',
  complete: '已完成',
}

const STATUS_COLORS: Record<string, string> = {
  interrupted: 'bg-orange-700 text-orange-100',
  paused: 'bg-warning text-warning-fg',
  error: 'bg-rose-700 text-rose-100',
  budget_limited: 'bg-orange-700 text-orange-100',
  early_stopped: 'bg-rose-800 text-rose-100',
  needs_refresh: 'bg-rose-800 text-rose-100',
  cancelled: 'bg-surface-raised text-secondary',
  complete: 'bg-emerald-700 text-emerald-100',
}

export function ContinueDialog({ open, summary, onClose, onContinue }: ContinueDialogProps) {
  const status = summary?.execution_status ?? 'unknown'
  const currentRound = summary?.current_round ?? 1
  // Backend semantics (scheduler.continue): for paused/interrupted the
  // mode/from_round payload is IGNORED — it always resumes at the
  // current round. Only offer what the backend will actually honor.
  const resumableOnly = status === 'paused' || status === 'interrupted'

  const [mode, setMode] = useState<ContinueMode>('restart')
  const [customRound, setCustomRound] = useState('')
  const [useCustom, setUseCustom] = useState(false)

  // Reset state when dialog opens
  useEffect(() => {
    if (open) {
      const defaultMode = STATUS_DEFAULT_MODE[status] ?? 'restart'
      setMode(defaultMode)
      setCustomRound('')
      setUseCustom(false)
    }
  }, [open, status])

  const handleSubmit = () => {
    if (resumableOnly) {
      // Backend resumes at the current round regardless of mode.
      onContinue('resume')
    } else if (useCustom && customRound) {
      onContinue('restart', parseInt(customRound, 10))
    } else if (mode === 'resume') {
      // "从当前轮次继续" on a restartable status must pass from_round —
      // without it the backend restarts from round 1 (高-2).
      onContinue('resume', currentRound)
    } else {
      onContinue(mode)
    }
    onClose()
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-96 rounded-xl border border-strong bg-slate-900 p-5 shadow-2xl">
        {/* Header */}
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground">继续研究</h3>
          <button onClick={onClose} className="text-muted hover:text-secondary">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Status + round info */}
        <div className="mb-4 flex items-center gap-2 text-xs">
          <span className={`rounded-full px-2 py-0.5 font-medium ${STATUS_COLORS[status] ?? 'bg-surface-raised text-secondary'}`}>
            {STATUS_LABELS[status] ?? status}
          </span>
          <span className="text-muted">当前轮次: Round {currentRound}</span>
        </div>

        {/* Mode selection */}
        <div className="space-y-2">
          {resumableOnly && (
            <p className="rounded border border-strong bg-surface px-2 py-1.5 text-[11px] text-muted">
              已暂停/已中断的研究只能从当前轮次（Round {currentRound}）恢复。
            </p>
          )}
          <label
            className={`flex cursor-pointer items-center gap-2 rounded-lg border p-2.5 transition-colors ${
              mode === 'resume' && !useCustom
                ? 'border-emerald-500/50 bg-emerald-900/20'
                : 'border-strong bg-surface hover:border'
            }`}
          >
            <input
              type="radio"
              name="mode"
              checked={mode === 'resume' && !useCustom}
              onChange={() => { setMode('resume'); setUseCustom(false) }}
              className="accent-emerald-500"
            />
            <Play className="h-3.5 w-3.5 text-success-fg" />
            <span className="text-xs text-foreground">从当前轮次继续 (Round {currentRound})</span>
          </label>

          <label
            className={`flex items-center gap-2 rounded-lg border p-2.5 transition-colors ${
              resumableOnly
                ? 'cursor-not-allowed border-subtle bg-surface-raised/30 opacity-40'
                : mode === 'restart' && !useCustom
                  ? 'cursor-pointer border-accent/50 bg-accent/10'
                  : 'cursor-pointer border-strong bg-surface hover:border'
            }`}
          >
            <input
              type="radio"
              name="mode"
              disabled={resumableOnly}
              checked={mode === 'restart' && !useCustom}
              onChange={() => { setMode('restart'); setUseCustom(false) }}
              className="accent-accent"
            />
            <RotateCcw className="h-3.5 w-3.5 text-accent-400" />
            <span className="text-xs text-foreground">从头开始 (Round 1)</span>
          </label>

          <label
            className={`flex items-center gap-2 rounded-lg border p-2.5 transition-colors ${
              resumableOnly
                ? 'cursor-not-allowed border-subtle bg-surface-raised/30 opacity-40'
                : useCustom
                  ? 'border-sky-500/50 bg-sky-900/20'
                  : 'cursor-pointer border-strong bg-surface hover:border'
            }`}
          >
            <input
              type="radio"
              name="mode"
              disabled={resumableOnly}
              checked={useCustom}
              onChange={() => setUseCustom(true)}
              className="accent-sky-500"
            />
            <span className="text-xs text-foreground">从指定轮次开始:</span>
            <input
              type="number"
              min={1}
              disabled={resumableOnly}
              value={customRound}
              onChange={(e) => {
                setCustomRound(e.target.value)
                setUseCustom(true)
              }}
              onFocus={() => setUseCustom(true)}
              placeholder="轮次"
              className="w-16 rounded border border bg-surface-raised px-2 py-0.5 text-xs text-foreground outline-none focus:border-sky-500 disabled:opacity-50"
            />
          </label>
        </div>

        {/* Actions */}
        <div className="mt-4 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg border border-strong px-3 py-1.5 text-xs text-muted hover:border hover:text-foreground"
          >
            取消
          </button>
          <button
            onClick={handleSubmit}
            className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500"
          >
            继续执行
          </button>
        </div>
      </div>
    </div>
  )
}
