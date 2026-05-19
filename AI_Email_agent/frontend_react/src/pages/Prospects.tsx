import { useEffect, useState, useCallback } from 'react'
import { Plus, Search, Trash2, Send, RefreshCw } from 'lucide-react'
import { prospectsApi } from '../api/client'
import type { Prospect, ProspectCreate } from '../api/client'
import { Table, Pagination } from '../components/ui/Table'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Modal } from '../components/ui/Modal'

function fmtDt(s?: string) {
  if (!s) return '—'
  return new Date(s).toLocaleDateString()
}

const STATUS_OPTIONS = ['', 'pending', 'contacted', 'interested', 'negotiating', 'scheduled', 'declined', 'closed']

export function Prospects() {
  const [data, setData] = useState<{ items: Prospect[]; total: number }>({ items: [], total: 0 })
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [addOpen, setAddOpen] = useState(false)
  const [outreachId, setOutreachId] = useState<number | null>(null)
  const [outreachMsg, setOutreachMsg] = useState('')
  const [deleting, setDeleting] = useState<number | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    prospectsApi.list({
      page, page_size: 20,
      search: search || undefined,
      status: statusFilter || undefined,
    }).then(setData).finally(() => setLoading(false))
  }, [page, search, statusFilter])

  useEffect(() => { load() }, [load])
  useEffect(() => { setPage(1) }, [search, statusFilter])

  const handleOutreach = async (id: number) => {
    setOutreachId(id)
    setOutreachMsg('Sending…')
    try {
      const r = await prospectsApi.triggerOutreach(id)
      setOutreachMsg(r.message)
      load()
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Failed'
      setOutreachMsg(`Error: ${msg}`)
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this prospect?')) return
    setDeleting(id)
    try {
      await prospectsApi.delete(id)
      load()
    } finally {
      setDeleting(null)
    }
  }

  const columns = [
    { key: 'name', header: 'Name', render: (r: Prospect) => <span className="font-medium text-slate-200">{r.name}</span> },
    { key: 'email', header: 'Email', render: (r: Prospect) => <span className="text-slate-400">{r.email}</span> },
    { key: 'company', header: 'Company', render: (r: Prospect) => <span className="text-slate-400">{r.company || '—'}</span> },
    { key: 'status', header: 'Status', render: (r: Prospect) => <Badge value={r.status} /> },
    { key: 'created_at', header: 'Added', render: (r: Prospect) => <span className="text-slate-500 text-xs">{fmtDt(r.created_at)}</span> },
    {
      key: 'actions', header: '', className: 'w-28',
      render: (r: Prospect) => (
        <div className="flex items-center gap-1">
          <Button
            size="sm" variant="ghost"
            onClick={() => handleOutreach(r.id)}
            loading={outreachId === r.id && outreachMsg === 'Sending…'}
            className="text-indigo-400 hover:text-indigo-300"
          >
            <Send size={13} />
          </Button>
          <Button
            size="sm" variant="ghost"
            onClick={() => handleDelete(r.id)}
            loading={deleting === r.id}
            className="text-red-400 hover:text-red-300"
          >
            <Trash2 size={13} />
          </Button>
        </div>
      )
    },
  ]

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-200">Prospects</h1>
          <p className="text-sm text-slate-500 mt-0.5">{data.total} total</p>
        </div>
        <Button variant="primary" onClick={() => setAddOpen(true)}>
          <Plus size={15} /> Add Prospect
        </Button>
      </div>

      {/* Filters */}
      <div className="flex gap-3">
        <div className="relative flex-1 max-w-xs">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search name or email…"
            className="w-full pl-9 pr-4 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#1a1d27] text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
          />
        </div>
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#1a1d27] text-slate-300 focus:outline-none focus:border-indigo-500"
        >
          {STATUS_OPTIONS.map(s => (
            <option key={s} value={s}>{s ? s.charAt(0).toUpperCase() + s.slice(1) : 'All statuses'}</option>
          ))}
        </select>
        <Button variant="ghost" size="sm" onClick={load}>
          <RefreshCw size={14} />
        </Button>
      </div>

      {/* Outreach result toast */}
      {outreachMsg && outreachMsg !== 'Sending…' && (
        <div className="px-4 py-2.5 rounded-lg border border-green-500/30 bg-green-500/10 text-green-300 text-sm flex justify-between">
          {outreachMsg}
          <button onClick={() => { setOutreachMsg(''); setOutreachId(null) }} className="text-green-500 hover:text-green-300">✕</button>
        </div>
      )}

      <Table columns={columns as never} data={data.items as never} loading={loading} />
      <Pagination page={page} total={data.total} pageSize={20} onChange={setPage} />

      <AddProspectModal open={addOpen} onClose={() => setAddOpen(false)} onCreated={() => { setAddOpen(false); load() }} />
    </div>
  )
}

function AddProspectModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState<ProspectCreate>({ name: '', email: '', company: '', timezone: 'UTC' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await prospectsApi.create(form)
      onCreated()
      setForm({ name: '', email: '', company: '', timezone: 'UTC' })
    } catch (ex: unknown) {
      const detail = (ex as { response?: { data?: { detail?: string } } }).response?.data?.detail
      setError(detail || 'Failed to create prospect.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Add Prospect">
      <form onSubmit={submit} className="space-y-4">
        {error && <p className="text-sm text-red-400 bg-red-400/10 px-3 py-2 rounded-lg">{error}</p>}
        {[
          { id: 'name', label: 'Name *', type: 'text', required: true },
          { id: 'email', label: 'Email *', type: 'email', required: true },
          { id: 'company', label: 'Company', type: 'text', required: false },
          { id: 'timezone', label: 'Timezone', type: 'text', required: false },
        ].map(f => (
          <div key={f.id}>
            <label className="block text-xs text-slate-400 mb-1">{f.label}</label>
            <input
              type={f.type}
              required={f.required}
              value={(form as never)[f.id] || ''}
              onChange={e => setForm(prev => ({ ...prev, [f.id]: e.target.value }))}
              className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500"
            />
          </div>
        ))}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={onClose} type="button">Cancel</Button>
          <Button variant="primary" type="submit" loading={loading}>Create Prospect</Button>
        </div>
      </form>
    </Modal>
  )
}
