/**
 * InterruptApprovalCard — approval card for HITL interrupts.
 *
 * Shown in StudyChat when a LangGraph engine interrupt fires
 * (e.g., novelty gate approval). User can approve or reject.
 */
import { useState, useCallback } from 'react'
import { Check, X, Loader2 } from 'lucide-react'
import { api } from '../../../../api/client'

interface InterruptApprovalCardProps {
  studyId: string
  interruptId: string
  hypothesis?: string
  message?: string
  onApproved?: () => void
  onRejected?: () => void
}

export function InterruptApprovalCard({
  studyId,
  interruptId,
  hypothesis,
  message,
  onApproved,
  onRejected,
}: InterruptApprovalCardProps) {
  const [status, setStatus] = useState<'pending' | 'approved' | 'rejected' | 'error'>('pending')
  const [loading, setLoading] = useState(false)

  const handleApprove = useCallback(async () => {
    setLoading(true)
    try {
      await api.post(`/study/${studyId}/interrupts/${interruptId}/respond`, {
        decision: 'approve',
      })
      setStatus('approved')
      onApproved?.()
    } catch (err) {
      console.error('Approve failed:', err)
      setStatus('error')
    } finally {
      setLoading(false)
    }
  }, [studyId, interruptId, onApproved])

  const handleReject = useCallback(async () => {
    setLoading(true)
    try {
      await api.post(`/study/${studyId}/interrupts/${interruptId}/respond`, {
        decision: 'reject',
      })
      setStatus('rejected')
      onRejected?.()
    } catch (err) {
      console.error('Reject failed:', err)
      setStatus('error')
    } finally {
      setLoading(false)
    }
  }, [studyId, interruptId, onRejected])

  if (status === 'approved') {
    return (
      <div className="rounded-lg border border-success-fg-muted bg-success-surface p-3">
        <div className="flex items-center gap-2 text-xs text-success-fg">
          <Check className="h-4 w-4" />
          <span>已批准</span>
        </div>
      </div>
    )
  }

  if (status === 'rejected') {
    return (
      <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-3">
        <div className="flex items-center gap-2 text-xs text-danger-fg">
          <X className="h-4 w-4" />
          <span>已拒绝</span>
        </div>
      </div>
    )
  }

  if (status === 'error') {
    return (
      <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-3">
        <div className="text-xs text-danger-fg">操作失败，请重试</div>
        <button
          onClick={() => setStatus('pending')}
          className="mt-1 text-[10px] text-danger-fg hover:text-rose-200"
        >
          重试
        </button>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-warning/30 bg-warning-fill p-3">
      <div className="mb-2 flex items-center gap-2 text-xs font-medium text-warning-fg">
        ⏳ 等待审批
      </div>

      {message && (
        <div className="mb-2 text-[11px] text-secondary">{message}</div>
      )}

      {hypothesis && (
        <div className="mb-3 rounded-md bg-surface p-2">
          <div className="mb-1 text-[10px] text-muted">假设</div>
          <div className="text-[11px] leading-relaxed text-secondary">{hypothesis}</div>
        </div>
      )}

      <div className="flex gap-2">
        <button
          onClick={handleApprove}
          disabled={loading}
          className="flex items-center gap-1 rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-success disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
          批准
        </button>
        <button
          onClick={handleReject}
          disabled={loading}
          className="flex items-center gap-1 rounded-md bg-rose-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-rose-500 disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <X className="h-3 w-3" />}
          拒绝
        </button>
      </div>
    </div>
  )
}
