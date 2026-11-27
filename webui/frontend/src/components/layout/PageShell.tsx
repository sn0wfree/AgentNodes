import { Moon, Sun } from 'lucide-react'
import { IconNav } from './IconNav'
import { useThemeStore } from '../../stores/theme'
import { getPageShellConfig } from '../chat/chatUiConfig'

interface PageShellProps {
  title: string
  subtitle?: string
  icon: React.ReactNode
  /** Extra actions rendered on the right of the sticky header. */
  actions?: React.ReactNode
  /** Sticky sub-header (e.g. factor zoo tabs + search). */
  stickyBar?: React.ReactNode
  children: React.ReactNode
}

/**
 * Shared page chrome for standalone pages: brand rail + sticky glass
 * header + centered (max-w-[1440px]) scrollable content.
 */
export function PageShell({
  title,
  subtitle,
  icon,
  actions,
  stickyBar,
  children,
}: PageShellProps) {
  const theme = useThemeStore((s) => s.theme)
  const toggleTheme = useThemeStore((s) => s.toggleTheme)
  const pageShellConfig = getPageShellConfig()

  return (
    <div className="relative flex h-screen overflow-hidden bg-app">
      <div>
        <div className="grid-layer" />
        <div />
        <div className="vignette-layer" />
        <div className="grain-layer" />
      </div>

      <div className="relative z-10 flex h-full w-full overflow-hidden">
        <IconNav />
        <div className="flex flex-1 flex-col overflow-hidden">
          <header className="glass sticky top-0 z-20 flex h-12 flex-shrink-0 items-center justify-between border-b border-subtle px-4">
            <div className="flex min-w-0 items-center gap-2.5">
              <span className="text-primary-400">{icon}</span>
              <h1 className="truncate text-sm font-semibold tracking-tight text-foreground">
                {title}
              </h1>
              {subtitle && (
                <span className="hidden truncate font-mono text-[10px] text-muted md:inline">
                  {subtitle}
                </span>
              )}
            </div>
            <div className="flex flex-shrink-0 items-center gap-2">
              {pageShellConfig.visibility.showThemeToggle && (
                <button
                  onClick={toggleTheme}
                  title={theme === 'dark' ? pageShellConfig.labels.themeLightLabel : pageShellConfig.labels.themeDarkLabel}
                  className="flex items-center gap-1.5 rounded-lg border border-strong bg-surface px-2.5 py-1.5 text-xs text-muted transition-colors hover:border-strong hover:text-secondary"
                >
                  {theme === 'dark' ? (
                    <Sun className="h-3.5 w-3.5" />
                  ) : (
                    <Moon className="h-3.5 w-3.5" />
                  )}
                </button>
              )}
              {actions}
            </div>
          </header>

          {stickyBar && (
            <div className="sticky top-12 z-10 flex-shrink-0 border-b border-subtle bg-background/70 backdrop-blur">
              {stickyBar}
            </div>
          )}

          <div className="flex-1 overflow-y-auto">
            <div className="mx-auto max-w-[1440px] px-6 py-5">{children}</div>
          </div>
        </div>
      </div>
    </div>
  )
}
