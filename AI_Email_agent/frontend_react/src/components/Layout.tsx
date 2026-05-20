import { NavLink, Outlet } from 'react-router-dom'
import {
  LayoutDashboard, Users, MessageSquare, Calendar,
  ScrollText, Settings, Bot, Circle
} from 'lucide-react'
import { clsx } from 'clsx'
import { useHealth } from '../hooks/useHealth'

const nav = [
  { to: '/',         label: 'Dashboard',  icon: LayoutDashboard },
  { to: '/prospects', label: 'Prospects', icon: Users },
  { to: '/threads',  label: 'Threads',    icon: MessageSquare },
  { to: '/meetings', label: 'Meetings',   icon: Calendar },
  { to: '/logs',     label: 'Logs',       icon: ScrollText },
  { to: '/config',   label: 'Config',     icon: Settings },
]

export function Layout() {
  const health = useHealth()

  return (
    <div className="flex h-screen overflow-hidden bg-[#0f1117]">
      {/* Sidebar */}
      <aside className="w-56 flex-shrink-0 flex flex-col border-r border-[#2a2d3e] bg-[#1a1d27]">
        {/* Logo */}
        <div className="px-5 py-5 border-b border-[#2a2d3e]">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-indigo-600 flex items-center justify-center">
              <Bot size={14} className="text-white" />
            </div>
            <div>
              <p className="text-sm font-bold text-slate-200 leading-tight">AI Email</p>
              <p className="text-xs text-slate-500 leading-tight">Agent</p>
            </div>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) => clsx(
                'flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors',
                isActive
                  ? 'bg-indigo-600/20 text-indigo-400 font-medium'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-[#20243a]'
              )}
            >
              <Icon size={16} />
              {label}
            </NavLink>
          ))}
        </nav>

        {/* API Status */}
        <div className="px-4 py-3 border-t border-[#2a2d3e]">
          <div className="flex items-center gap-2">
            <Circle
              size={8}
              className={health?.status === 'ok' ? 'fill-green-400 text-green-400' : 'fill-red-400 text-red-400'}
            />
            <span className="text-xs text-slate-500">
              {health ? `API ${health.status === 'ok' ? 'online' : 'degraded'}` : 'Connecting…'}
            </span>
          </div>
          {health && (
            <div className="mt-1 space-y-0.5">
              <p className="text-xs text-slate-600">
                DB: <span className={health.db === 'connected' ? 'text-green-500' : 'text-red-500'}>{health.db}</span>
                {' · '}
                Redis: <span className={health.redis === 'connected' ? 'text-green-500' : 'text-red-500'}>{health.redis ?? '…'}</span>
              </p>
              {health.version && <p className="text-xs text-slate-600">v{health.version}</p>}
            </div>
          )}
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
