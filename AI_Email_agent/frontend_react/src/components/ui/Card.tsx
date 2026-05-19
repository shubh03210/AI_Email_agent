import { clsx } from 'clsx'
import { ReactNode } from 'react'

interface CardProps {
  children: ReactNode
  className?: string
  title?: string
  action?: ReactNode
}

export function Card({ children, className, title, action }: CardProps) {
  return (
    <div className={clsx(
      'rounded-xl border border-[#2a2d3e] bg-[#1a1d27] p-5',
      className
    )}>
      {(title || action) && (
        <div className="flex items-center justify-between mb-4">
          {title && <h3 className="text-sm font-semibold text-slate-300 uppercase tracking-wider">{title}</h3>}
          {action}
        </div>
      )}
      {children}
    </div>
  )
}

interface MetricCardProps {
  label: string
  value: string | number
  sub?: string
  icon?: ReactNode
  color?: string
}

export function MetricCard({ label, value, sub, icon, color = 'text-indigo-400' }: MetricCardProps) {
  return (
    <div className="rounded-xl border border-[#2a2d3e] bg-[#1a1d27] p-5 flex items-start gap-4">
      {icon && (
        <div className={clsx('p-2 rounded-lg bg-[#0f1117]', color)}>
          {icon}
        </div>
      )}
      <div>
        <p className="text-xs text-slate-500 uppercase tracking-wider">{label}</p>
        <p className={clsx('text-2xl font-bold mt-0.5', color)}>{value}</p>
        {sub && <p className="text-xs text-slate-500 mt-0.5">{sub}</p>}
      </div>
    </div>
  )
}
