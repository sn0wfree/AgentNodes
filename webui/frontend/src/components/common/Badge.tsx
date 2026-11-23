import { type ReactNode } from 'react'

interface BadgeProps {
  children: ReactNode
  variant?: 'default' | 'success' | 'warning' | 'error' | 'info'
  size?: 'sm' | 'md'
}

const VARIANT_CLASSES = {
  default: 'bg-slate-700 text-slate-200',
  success: 'bg-emerald-900/50 text-emerald-300',
  warning: 'bg-warning-fill text-warning-fg',
  error: 'bg-red-900/50 text-red-300',
  info: 'bg-blue-900/50 text-blue-300',
}

export function Badge({ children, variant = 'default', size = 'sm' }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center rounded-full font-medium
        ${VARIANT_CLASSES[variant]}
        ${size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-2.5 py-1 text-sm'}
      `}
    >
      {children}
    </span>
  )
}
