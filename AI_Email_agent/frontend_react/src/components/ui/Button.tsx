import { clsx } from 'clsx'
import { ReactNode } from 'react'

interface ButtonProps {
  children: ReactNode
  onClick?: () => void
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
  size?: 'sm' | 'md'
  disabled?: boolean
  loading?: boolean
  type?: 'button' | 'submit'
  className?: string
}

const variants = {
  primary:   'bg-indigo-600 hover:bg-indigo-500 text-white border-transparent',
  secondary: 'bg-[#20243a] hover:bg-[#2a2d3e] text-slate-300 border-[#2a2d3e]',
  danger:    'bg-red-600/20 hover:bg-red-600/40 text-red-400 border-red-500/30',
  ghost:     'bg-transparent hover:bg-[#20243a] text-slate-400 border-transparent',
}

const sizes = {
  sm: 'px-3 py-1.5 text-xs',
  md: 'px-4 py-2 text-sm',
}

export function Button({
  children, onClick, variant = 'secondary', size = 'md',
  disabled, loading, type = 'button', className
}: ButtonProps) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || loading}
      className={clsx(
        'inline-flex items-center gap-2 font-medium rounded-lg border transition-colors',
        'disabled:opacity-40 disabled:cursor-not-allowed',
        variants[variant], sizes[size], className
      )}
    >
      {loading && <div className="w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin" />}
      {children}
    </button>
  )
}
