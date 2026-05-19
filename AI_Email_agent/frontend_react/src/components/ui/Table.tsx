import { clsx } from 'clsx'
import { ReactNode } from 'react'

interface Column<T> {
  key: string
  header: string
  render?: (row: T) => ReactNode
  className?: string
}

interface TableProps<T> {
  columns: Column<T>[]
  data: T[]
  onRowClick?: (row: T) => void
  emptyMessage?: string
  loading?: boolean
}

export function Table<T extends Record<string, unknown>>({
  columns, data, onRowClick, emptyMessage = 'No data found.', loading
}: TableProps<T>) {
  return (
    <div className="overflow-x-auto rounded-xl border border-[#2a2d3e]">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[#2a2d3e] bg-[#0f1117]">
            {columns.map(col => (
              <th key={col.key} className={clsx(
                'px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider',
                col.className
              )}>
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <tr><td colSpan={columns.length} className="px-4 py-8 text-center text-slate-500">
              <div className="flex items-center justify-center gap-2">
                <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                Loading…
              </div>
            </td></tr>
          ) : data.length === 0 ? (
            <tr><td colSpan={columns.length} className="px-4 py-8 text-center text-slate-500">{emptyMessage}</td></tr>
          ) : data.map((row, i) => (
            <tr
              key={i}
              onClick={() => onRowClick?.(row)}
              className={clsx(
                'border-b border-[#2a2d3e] bg-[#1a1d27] transition-colors',
                onRowClick && 'cursor-pointer hover:bg-[#20243a]'
              )}
            >
              {columns.map(col => (
                <td key={col.key} className={clsx('px-4 py-3 text-slate-300', col.className)}>
                  {col.render ? col.render(row) : String(row[col.key] ?? '—')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

interface PaginationProps {
  page: number
  total: number
  pageSize: number
  onChange: (page: number) => void
}

export function Pagination({ page, total, pageSize, onChange }: PaginationProps) {
  const totalPages = Math.ceil(total / pageSize)
  if (totalPages <= 1) return null
  return (
    <div className="flex items-center justify-between mt-4 text-sm text-slate-400">
      <span>{total} total</span>
      <div className="flex gap-1">
        <button
          onClick={() => onChange(page - 1)}
          disabled={page <= 1}
          className="px-3 py-1 rounded border border-[#2a2d3e] bg-[#1a1d27] hover:bg-[#20243a] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          ← Prev
        </button>
        <span className="px-3 py-1">{page} / {totalPages}</span>
        <button
          onClick={() => onChange(page + 1)}
          disabled={page >= totalPages}
          className="px-3 py-1 rounded border border-[#2a2d3e] bg-[#1a1d27] hover:bg-[#20243a] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          Next →
        </button>
      </div>
    </div>
  )
}
