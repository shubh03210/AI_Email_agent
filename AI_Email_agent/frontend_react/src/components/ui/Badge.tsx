import { clsx } from 'clsx'

const colors: Record<string, string> = {
  // prospect status
  pending:     'bg-yellow-500/20 text-yellow-300 border-yellow-500/30',
  contacted:   'bg-blue-500/20 text-blue-300 border-blue-500/30',
  interested:  'bg-green-500/20 text-green-300 border-green-500/30',
  negotiating: 'bg-orange-500/20 text-orange-300 border-orange-500/30',
  scheduled:   'bg-cyan-500/20 text-cyan-300 border-cyan-500/30',
  declined:    'bg-red-500/20 text-red-300 border-red-500/30',
  closed:      'bg-gray-500/20 text-gray-400 border-gray-500/30',
  // thread status
  active:      'bg-green-500/20 text-green-300 border-green-500/30',
  waiting:     'bg-yellow-500/20 text-yellow-300 border-yellow-500/30',
  // meeting status
  confirmed:   'bg-cyan-500/20 text-cyan-300 border-cyan-500/30',
  cancelled:   'bg-red-500/20 text-red-300 border-red-500/30',
  rescheduled: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
  // agent run status
  success:     'bg-green-500/20 text-green-300 border-green-500/30',
  error:       'bg-red-500/20 text-red-300 border-red-500/30',
  running:     'bg-blue-500/20 text-blue-300 border-blue-500/30',
  // misc
  ok:          'bg-green-500/20 text-green-300 border-green-500/30',
  degraded:    'bg-red-500/20 text-red-300 border-red-500/30',
  queued:      'bg-purple-500/20 text-purple-300 border-purple-500/30',
  sent:        'bg-green-500/20 text-green-300 border-green-500/30',
}

interface BadgeProps {
  value: string
  className?: string
}

export function Badge({ value, className }: BadgeProps) {
  const key = (value || '').toLowerCase()
  const color = colors[key] ?? 'bg-gray-500/20 text-gray-300 border-gray-500/30'
  return (
    <span className={clsx(
      'inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border',
      color, className
    )}>
      {value}
    </span>
  )
}
