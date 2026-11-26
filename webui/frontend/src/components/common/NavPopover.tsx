import { useState } from 'react'
import * as Popover from '@radix-ui/react-popover'
import type { ReactNode } from 'react'

interface NavPopoverProps {
  trigger: ReactNode
  children: ReactNode
  side?: 'right' | 'top'
  align?: 'start' | 'center' | 'end'
}

export function NavPopover({
  trigger,
  children,
  side = 'right',
  align = 'start',
}: NavPopoverProps) {
  const [open, setOpen] = useState(false)

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        {trigger}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          side={side}
          align={align}
          sideOffset={8}
          className="z-50 rounded-lg border border-strong bg-slate-900 shadow-2xl"
        >
          {children}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

interface PopoverItemProps {
  icon?: ReactNode
  label: string
  description?: string
  active?: boolean
  onClick?: () => void
}

export function PopoverItem({ icon, label, description, active, onClick }: PopoverItemProps) {
  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs transition-colors
        ${active ? 'bg-primary-600/20 text-primary-100' : 'text-secondary hover:bg-surface-raised'}
      `}
    >
      {icon && <span className="flex-shrink-0">{icon}</span>}
      <div className="flex-1 min-w-0">
        <div className="truncate font-medium">{label}</div>
        {description && (
          <div className="truncate text-[10px] text-muted">{description}</div>
        )}
      </div>
    </button>
  )
}