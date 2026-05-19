import { useEffect, useState, useCallback } from 'react'
import { Play, ChevronRight, RefreshCw } from 'lucide-react'
import { threadsApi } from '../api/client'
import type { EmailThread, EmailMessage } from '../api/client'
import { Table, Pagination } from '../components/ui/Table'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Modal } from '../components/ui/Modal'
import { Card } from '../components/ui/Card'

function fmtDt(s?: string) {
  if (!s) return '—'
  return new Date(s).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
}

export function Threads() {
  const [data, setData] = useState<{ items: EmailThread[]; total: number }>({ items: [], total: 0 })
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<EmailThread | null>(null)
  const [runningId, setRunningId] = useState<number | null>(null)
  const [runResult, setRunResult] = useState<Record<number, string>>({})

  const load = useCallback(() => {
    setLoading(true)
    threadsApi.list({ page, page_size: 20 }).then(setData).finally(() => setLoading(false))
  }, [page])

  useEffect(() => { load() }, [load])

  const handleRunAgent = async (id: number, e: React.MouseEvent) => {
    e.stopPropagation()
    setRunningId(id)
    try {
      const r = await threadsApi.runAgent(id)
      setRunResult(prev => ({ ...prev, [id]: r.message }))
      load()
    } catch (ex: unknown) {
      const msg = (ex as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Failed'
      setRunResult(prev => ({ ...prev, [id]: `Error: ${msg}` }))
    } finally {
      setRunningId(null)
    }
  }

  const columns = [
    { key: 'id', header: '#', className: 'w-12', render: (r: EmailThread) => <span className="text-slate-500">#{r.id}</span> },
    { key: 'subject', header: 'Subject', render: (r: EmailThread) => (
      <div>
        <p className="text-slate-200 font-medium truncate max-w-xs">{r.subject}</p>
        {runResult[r.id] && (
          <p className="text-xs text-indigo-400 mt-0.5 truncate max-w-xs">{runResult[r.id]}</p>
        )}
      </div>
    )},
    { key: 'status', header: 'Status', render: (r: EmailThread) => <Badge value={r.status} /> },
    { key: 'follow_up_count', header: 'Follow-ups', render: (r: EmailThread) => (
      <span className="text-slate-400">{r.follow_up_count}</span>
    )},
    { key: 'updated_at', header: 'Last Activity', render: (r: EmailThread) => (
      <span className="text-slate-500 text-xs">{fmtDt(r.updated_at)}</span>
    )},
    { key: 'actions', header: '', className: 'w-24', render: (r: EmailThread) => (
      <div className="flex items-center gap-1">
        <Button size="sm" variant="ghost" className="text-indigo-400" onClick={e => handleRunAgent(r.id, e)} loading={runningId === r.id}>
          <Play size={13} />
        </Button>
        <Button size="sm" variant="ghost" className="text-slate-400" onClick={() => setSelected(r)}>
          <ChevronRight size={13} />
        </Button>
      </div>
    )},
  ]

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-200">Threads</h1>
          <p className="text-sm text-slate-500 mt-0.5">{data.total} email threads</p>
        </div>
        <Button variant="ghost" size="sm" onClick={load}><RefreshCw size={14} /></Button>
      </div>

      <Table columns={columns as never} data={data.items as never} loading={loading} onRowClick={r => setSelected(r as EmailThread)} />
      <Pagination page={page} total={data.total} pageSize={20} onChange={setPage} />

      {selected && (
        <ThreadDetailModal thread={selected} onClose={() => setSelected(null)} onRunAgent={id => handleRunAgent(id, { stopPropagation: () => {} } as React.MouseEvent)} runningId={runningId} />
      )}
    </div>
  )
}

function ThreadDetailModal({ thread, onClose, onRunAgent, runningId }: {
  thread: EmailThread
  onClose: () => void
  onRunAgent: (id: number) => void
  runningId: number | null
}) {
  const [messages, setMessages] = useState<EmailMessage[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    threadsApi.messages(thread.id, { page_size: 50 })
      .then(r => setMessages(r.items))
      .finally(() => setLoading(false))
  }, [thread.id])

  return (
    <Modal open title={`Thread #${thread.id}: ${thread.subject}`} onClose={onClose} width="max-w-2xl">
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <Badge value={thread.status} />
          <span className="text-xs text-slate-500">Follow-ups: {thread.follow_up_count}</span>
          <Button size="sm" variant="primary" onClick={() => onRunAgent(thread.id)} loading={runningId === thread.id}>
            <Play size={12} /> Run Agent
          </Button>
        </div>

        <Card title="Messages">
          {loading ? (
            <p className="text-sm text-slate-500">Loading…</p>
          ) : messages.length === 0 ? (
            <p className="text-sm text-slate-500">No messages yet.</p>
          ) : (
            <div className="space-y-3 max-h-80 overflow-y-auto pr-1">
              {messages.map(m => (
                <div key={m.id} className={`flex ${m.sender === 'agent' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[80%] px-4 py-2.5 rounded-2xl text-sm ${
                    m.sender === 'agent'
                      ? 'bg-indigo-600/30 text-indigo-100 rounded-tr-sm'
                      : 'bg-[#20243a] text-slate-300 rounded-tl-sm'
                  }`}>
                    <p className="text-xs opacity-60 mb-1">{m.sender} · {fmtDt(m.timestamp)}</p>
                    <p className="whitespace-pre-wrap leading-relaxed">{m.body}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </Modal>
  )
}
