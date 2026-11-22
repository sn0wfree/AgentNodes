import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import {
  MessageSquare,
  Network,
  Sigma,
  Layers,
  BookOpen,
  Library,
  Settings,
  LogOut,
  ShieldCheck,
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

const NAV_ITEMS: NavEntry[] = [
  { to: '/chat', label: 'Chat', icon: MessageSquare, toggleSidebar: true },
  { to: '/dag', label: '编排', icon: Network },
  { to: '/study', label: 'Study', icon: BookOpen },
  { to: '/factors', label: '因子库', icon: Sigma },
  { to: '/strategies', label: '策略库', icon: Layers },
]

export function IconNav() {
  const setSettingsOpen = useLayoutStore((s) => s.setSettingsOpen)
  const toggleSidebar = useLayoutStore((s) => s.toggleSidebar)
  const logout = useAuthStore((s) => s.logout)
  const user = useAuthStore((s) => s.user)
  const navigate = useNavigate()
  const location = useLocation()
  const isHome = location.pathname === '/'
  const isAdmin = user?.role === 'admin'

  return (
    <nav className="flex h-screen w-16 flex-shrink-0 flex-col items-center gap-1 border-r border-slate-800 bg-slate-900/50 py-3">
      {/* Brand / logo — doubles as the home (dashboard) entry */}
      <button
        onClick={() => navigate('/')}
        title="AgentNodes · 首页"
        className={`group relative mb-3 flex h-10 w-10 items-center justify-center rounded-xl bg-slate-800 transition-colors hover:bg-slate-700 ${
          isHome ? 'ring-2 ring-sky-500' : ''
        }`}
      >
        {isHome && (
          <span className="absolute -left-[9px] top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-full bg-sky-500" />
        )}
        <span className="font-sans text-sm font-bold text-slate-100">AN</span>
        <span className="pointer-events-none absolute left-[52px] top-1/2 -translate-y-1/2 whitespace-nowrap rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-[11px] text-slate-200 opacity-0 shadow-lg transition-opacity group-hover:opacity-100">
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
                  ? 'bg-slate-800 text-slate-100'
                  : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
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

      {/* 知识库 — placeholder, planned for a later release */}
      <button
        title="知识库 · 规划中"
        disabled
        className="flex h-10 w-10 cursor-not-allowed items-center justify-center rounded-lg text-slate-600 opacity-40"
      >
        <Library className="h-5 w-5" />
      </button>

      {isAdmin && (
        <NavLink
          to="/admin/users"
          title="用户管理"
          className={({ isActive }) =>
            `group relative mt-1 flex h-10 w-10 items-center justify-center rounded-lg transition-colors
            ${
              isActive
                ? 'bg-slate-800 text-slate-100'
                : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
            }`
          }
        >
          {({ isActive }) => (
            <>
              {isActive && (
                <span className="absolute -left-[9px] top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-full bg-sky-500" />
              )}
              <ShieldCheck className="h-5 w-5" />
            </>
          )}
        </NavLink>
      )}

      <div className="flex-1" />

      <button
        onClick={() => setSettingsOpen(true)}
        title="设置"
        className="flex h-10 w-10 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-200"
      >
        <Settings className="h-5 w-5" />
      </button>
      <button
        onClick={() => {
          logout()
          navigate('/login')
        }}
        title="登出"
        className="flex h-10 w-10 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-200"
      >
        <LogOut className="h-5 w-5" />
      </button>
    </nav>
  )
}
