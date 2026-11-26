import { useToastStore } from '../../stores/toast'
import { X, CheckCircle, AlertCircle, Info, AlertTriangle } from 'lucide-react'

const ICONS = {
  success: CheckCircle,
  error: AlertCircle,
  warning: AlertTriangle,
  info: Info,
}

const COLORS = {
  success: 'border-emerald-500/30 bg-emerald-950/50',
  error: 'border-red-500/30 bg-red-950/50',
  warning: 'border-warning/30 bg-warning-fill',
  info: 'border-blue-500/30 bg-blue-950/50',
}

const ICON_COLORS = {
  success: 'text-success-fg',
  error: 'text-danger-fg',
  warning: 'text-warning-fg',
  info: 'text-info-fg',
}

export function ToastManager() {
  const toasts = useToastStore((s) => s.toasts)
  const removeToast = useToastStore((s) => s.removeToast)

  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
      {toasts.map((toast) => {
        const Icon = ICONS[toast.type]
        return (
          <div
            key={toast.id}
            className={`flex items-center gap-3 rounded-lg border px-4 py-3 shadow-lg backdrop-blur-md
              ${COLORS[toast.type]}
            `}
          >
            <Icon className={`h-4 w-4 flex-shrink-0 ${ICON_COLORS[toast.type]}`} />
            <span className="text-sm text-foreground">{toast.message}</span>
            <button
              onClick={() => removeToast(toast.id)}
              className="ml-2 text-muted hover:text-foreground"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        )
      })}
    </div>
  )
}
