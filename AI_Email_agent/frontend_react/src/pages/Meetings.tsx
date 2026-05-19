import { useEffect, useState, useCallback } from 'react'
import { XCircle, RefreshCw, Calendar } from 'lucide-react'
import { meetingsApi } from '../api/client'
import type { Meeting } from '../api/client'
import { Table, Pagination } from '../components/ui/Table'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'

function fmtDt(s?: string) {
  if (!s) return '—'
  return new Date(s).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

const STATUS_OPTIONS = ['', 'confirmed', 'cancelled', 'rescheduled']

export function Meetings() {
  const [data, setData] = useState<{ items: Meeting[]; total: number }>({ items: [], total: 0 })
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [actionId, setActionId] = useState<number | null>(null)
  const [toast, setToast] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    meetingsApi.list({ page, page_size: 20, status: statusFilter || undefined })
      .then(setData).finally(() => setLoading(false))
  }, [page, statusFilter])

  useEffect(() => { load() }, [load])

  const handleCancel = async (id: number) => {
    if (!confirm('Cancel this meeting?')) return
    setActionId(id)
    try {
      await meetingsApi.cancel(id)
      setToast('Meeting cancelled.')
      load()
    } catch {
      setToast('Failed to cancel.')
    } finally {
      setActionId(null)
    }
  }

  const handleReschedule = async (id: number) => {
    setActionId(id)
    try {
      const r = await meetingsApi.reschedule(id)
      setToast(r.message)
      load()
    } catch {
      setToast('Failed to trigger reschedule.')
    } finally {
      setActionId(null)
    }
  }

  const columns = [
    { key: 'id', header: '#', className: 'w-12', render: (r: Meeting) => <span className="text-slate-500">#{r.id}</span> },
    { key: 'scheduled_at', header: 'Scheduled', render: (r: Meeting) => (
      <span className="text-slate-200">{fmtDt(r.scheduled_at)}</span>
    )},
    { key: 'status', header: 'Status', render: (r: Meeting) => <Badge value={r.status} /> },
    { key: 'google_event_id', header: 'GCal Event', render: (r: Meeting) => (
      <span className="text-slate-500 font-mono text-xs">{r.google_event_id || '—'}</span>
    )},
    { key: 'thread_id', header: 'Thread', render: (r: Meeting) => (
      <span className="text-slate-400">#{r.thread_id}</span>
    )},
    { key: 'created_at', header: 'Booked', render: (r: Meeting) => (
      <span className="text-slate-500 text-xs">{fmtDt(r.created_at)}</span>
    )},
    { key: 'actions', header: '', className: 'w-32', render: (r: Meeting) => (
      r.status !== 'cancelled' ? (
        <div className="flex items-center gap-1">
          <Button size="sm" variant="ghost" className="text-amber-400" onClick={() => handleReschedule(r.id)} loading={actionId === r.id}>
            <RefreshCw size={13} />
          </Button>
          <Button size="sm" variant="ghost" className="text-red-400" onClick={() => handleCancel(r.id)} loading={actionId === r.id}>
            <XCircle size={13} />
          </Button>
        </div>
      ) : null
    )},
  ]

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-200">Meetings</h1>
          <p className="text-sm text-slate-500 mt-0.5">{data.total} total</p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
            className="px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#1a1d27] text-slate-300 focus:outline-none focus:border-indigo-500"
          >
            {STATUS_OPTIONS.map(s => (
              <option key={s} value={s}>{s ? s.charAt(0).toUpperCase() + s.slice(1) : 'All statuses'}</option>
            ))}
          </select>
          <Button variant="ghost" size="sm" onClick={load}><RefreshCw size={14} /></Button>
        </div>
      </div>

      {toast && (
        <div className="px-4 py-2.5 rounded-lg border border-indigo-500/30 bg-indigo-500/10 text-indigo-300 text-sm flex justify-between">
          <div className="flex items-center gap-2"><Calendar size={14} />{toast}</div>
          <button onClick={() => setToast('')} className="text-indigo-400">✕</button>
        </div>
      )}

      <Table columns={columns as never} data={data.items as never} loading={loading} emptyMessage="No meetings booked yet." />
      <Pagination page={page} total={data.total} pageSize={20} onChange={setPage} />
    </div>
  )
}
