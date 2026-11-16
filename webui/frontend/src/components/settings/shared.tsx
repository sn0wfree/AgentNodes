import type { LucideIcon } from 'lucide-react'
import { Check, EyeOff, Eye } from 'lucide-react'

export function Section({
  icon: Icon,
  title,
  children,
}: {
  icon: LucideIcon
  title: string
  children: React.ReactNode
}) {
  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <Icon className="h-4 w-4 text-primary-400" />
        <h3 className="text-sm font-medium text-slate-200">{title}</h3>
      </div>
      {children}
    </div>
  )
}

export function PasswordInput({
  placeholder,
  value,
  onChange,
  show,
  onToggle,
}: {
  placeholder: string
  value: string
  onChange: (v: string) => void
  show: boolean
  onToggle: () => void
}) {
  return (
    <div className="relative">
      <input
        type={show ? 'text' : 'password'}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 pr-10 text-sm text-slate-100 outline-none focus:border-primary-500"
      />
      <button
        type="button"
        onClick={onToggle}
        className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-200"
      >
        {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  )
}

export function ThemeBtn({
  label,
  active,
  onClick,
}: {
  label: string
  active?: boolean
  onClick?: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-lg px-4 py-1.5 text-sm transition-colors ${
        active ? 'bg-primary-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
      }`}
    >
      {label}
    </button>
  )
}

export function SizeBtn({
  label,
  active,
  onClick,
  disabled,
}: {
  label: string
  active?: boolean
  onClick?: () => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      className={`rounded-lg px-4 py-1.5 text-sm transition-colors ${
        active ? 'bg-primary-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
      } ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
    >
      {label}
    </button>
  )
}

export function LayoutOption({
  label,
  desc,
  active,
  onClick,
}: {
  label: string
  desc: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-lg border p-3 text-left transition-colors ${
        active
          ? 'border-primary-500 bg-primary-600/10'
          : 'border-slate-700 bg-slate-900/50 hover:bg-slate-800/50'
      }`}
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-slate-200">{label}</span>
        {active && <Check className="h-3.5 w-3.5 text-primary-400" />}
      </div>
      <div className="mt-1 text-xs text-slate-500">{desc}</div>
    </button>
  )
}