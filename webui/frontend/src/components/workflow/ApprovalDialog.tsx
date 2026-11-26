import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Check, X, Loader2 } from 'lucide-react'

interface ApprovalDialogProps {
  open: boolean
  runId: string
  planPreview: string
  busy: boolean
  onApprove: (edits?: string) => void
  onReject: (edits?: string) => void
  onClose: () => void
}

export function ApprovalDialog({
  open,
  runId,
  planPreview,
  busy,
  onApprove,
  onReject,
  onClose,
}: ApprovalDialogProps) {
  const [edits, setEdits] = useState('')

  return (
    <Dialog.Root open={open} onOpenChange={(v) => !v && !busy && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[480px] max-w-[92vw] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-strong bg-background p-5 shadow-2xl">
          <Dialog.Title className="text-sm font-medium text-foreground">
            工作流等待人工确认
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-[11px] text-muted">
            run: <span className="text-secondary">{runId}</span> — 计划已生成，执行已暂停。
          </Dialog.Description>

          <div className="mt-3 max-h-40 overflow-y-auto rounded border border-subtle bg-slate-950 p-2.5 text-[11px] leading-relaxed text-secondary">
            {planPreview || '（无计划摘要）'}
          </div>

          <div className="mt-3">
            <label className="mb-1 block text-[10px] text-muted">
              编辑意见（可选，拒绝时作为重规划依据）
            </label>
            <textarea
              value={edits}
              onChange={(e) => setEdits(e.target.value)}
              rows={2}
              placeholder="例：换一个方向，先验证数据覆盖再回测"
              className="w-full rounded border border-strong bg-slate-950 px-2 py-1 text-xs text-foreground outline-none focus:border-primary-500"
            />
          </div>

          <div className="mt-4 flex justify-end gap-2">
            <button
              onClick={() => onReject(edits.trim() || undefined)}
              disabled={busy}
              className="inline-flex items-center gap-1 rounded border border-rose-800 bg-rose-950/40 px-3 py-1.5 text-xs text-rose-300 hover:bg-rose-950 disabled:opacity-50"
            >
              {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <X className="h-3 w-3" />}
              拒绝并重规划
            </button>
            <button
              onClick={() => onApprove(edits.trim() || undefined)}
              disabled={busy}
              className="inline-flex items-center gap-1 rounded bg-emerald-600 px-3 py-1.5 text-xs text-white hover:bg-success disabled:opacity-50"
            >
              {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
              批准执行
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
