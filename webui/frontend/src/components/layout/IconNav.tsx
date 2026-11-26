import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import {
  MessageSquare,
  Activity,
  Settings,
  LogOut,
} from 'lucide-react'
import { useLayoutStore } from '../../stores/layout'
import { useAuthStore } from '../../stores/auth'

interface NavEntry {
  to: string
  label: string
  icon: typeof MessageSquare
  end?: boolean
  toggleSidebar?: boolean
}

/**
 * 导航侧栏（2026-11-23）：Chat / Trace / 账户与设置；DAG 入口已撤销（路由/API 保留）。
 * Study · 因子库 · 策略库 · 知识库图标按 §8.1「不暴露假入口」原则移除
 * —— 路由与页面保持可达（URL 直入），后端契约落地后再恢复图标；
 * 恢复点：git show 887e3068:webui/frontend/src/components/layout/IconNav.tsx。
 */
const NAV_ITEMS: NavEntry[] = [
  { to: '/chat', label: 'Chat', icon: MessageSquare, toggleSidebar: true },
  { to: '/trace', label: 'Trace', icon: Activity },
]

export function IconNav() {
  const toggleSidebar = useLayoutStore((s) => s.toggleSidebar)
  const logout = useAuthStore((s) => s.logout)
  const navigate = useNavigate()
  const location = useLocation()
  const isHome = location.pathname === '/'

  return (
    <nav className="flex h-screen w-16 flex-shrink-0 flex-col items-center gap-1 border-r border-subtle bg-slate-900/50 py-3">
      {/* Brand / logo — doubles as the home (dashboard) entry */}
      <button
        onClick={() => navigate('/')}
        title="AgentNodes · 首页"
        className={`group relative mb-3 flex h-10 w-10 items-center justify-center rounded-xl bg-surface-raised transition-colors hover:bg-surface-raised ${
          isHome ? 'ring-2 ring-sky-500' : ''
        }`}
      >
        {isHome && (
          <span className="absolute -left-[9px] top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-full bg-sky-500" />
        )}
        <span className="font-sans text-sm font-bold text-foreground">AN</span>
        <span className="pointer-events-none absolute left-[52px] top-1/2 -translate-y-1/2 whitespace-nowrap rounded-md border border-strong bg-surface-raised px-2 py-1 text-[11px] text-foreground opacity-0 shadow-lg transition-opacity group-hover:opacity-100">
          {isHome ? 'AgentNodes · 首页' : 'AgentNodes'}
        </span>
      </button>

      {NAV_ITEMS.map((item) => {
        const Icon = item.icon
        return (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={() => {
              if (item.toggleSidebar && location.pathname === item.to) toggleSidebar()
            }}
            title={item.label}
            className={({ isActive }) =>
              `group relative flex h-10 w-10 items-center justify-center rounded-lg transition-colors
              ${
                isActive
                  ? 'bg-surface-raised text-foreground'
                  : 'text-muted hover:bg-surface-raised hover:text-foreground'
              }`
            }
          >
            {({ isActive }) => (
              <>
                {isActive && (
                  <span className="absolute -left-[9px] top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-full bg-sky-500" />
                )}
                <Icon className="h-5 w-5" />
              </>
            )}
          </NavLink>
        )
      })}

      <div className="flex-1" />

      <NavLink
        to="/account"
        title="账户与设置"
        className={({ isActive }) =>
          `group relative flex h-10 w-10 items-center justify-center rounded-lg transition-colors ${
            isActive
              ? 'bg-surface-raised text-foreground'
              : 'text-muted hover:bg-surface-raised hover:text-foreground'
          }`
        }
      >
        {({ isActive }) => (
          <>
            {isActive && (
              <span className="absolute -left-[9px] top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-full bg-sky-500" />
            )}
            <Settings className="h-5 w-5" />
          </>
        )}
      </NavLink>
      <button
        onClick={() => {
          logout()
          navigate('/login')
        }}
        title="登出"
        className="flex h-10 w-10 items-center justify-center rounded-lg text-muted transition-colors hover:bg-surface-raised hover:text-foreground"
      >
        <LogOut className="h-5 w-5" />
      </button>
    </nav>
  )
}
