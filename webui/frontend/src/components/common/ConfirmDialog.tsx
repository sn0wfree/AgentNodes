import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'

interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirmLabel?: string
  cancelLabel?: string
  variant?: 'danger' | 'default'
  onConfirm: () => void
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = '确认',
  cancelLabel = '取消',
  variant = 'default',
  onConfirm,
}: ConfirmDialogProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-xl bg-surface-raised p-6 shadow-2xl">
          <Dialog.Title className="text-lg font-semibold text-foreground">
            {title}
          </Dialog.Title>
          <Dialog.Description className="mt-2 text-sm text-muted">
            {description}
          </Dialog.Description>
          <div className="mt-6 flex justify-end gap-3">
            <Dialog.Close asChild>
              <button className="rounded-lg px-4 py-2 text-sm text-secondary hover:bg-surface-raised">
                {cancelLabel}
              </button>
            </Dialog.Close>
            <button
              onClick={onConfirm}
              className={`rounded-lg px-4 py-2 text-sm font-medium text-white
                ${variant === 'danger' ? 'bg-red-600 hover:bg-red-700' : 'bg-primary-600 hover:bg-primary-700'}
              `}
            >
              {confirmLabel}
            </button>
          </div>
          <Dialog.Close asChild>
            <button className="absolute right-4 top-4 text-muted hover:text-foreground">
              <X className="h-4 w-4" />
            </button>
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
