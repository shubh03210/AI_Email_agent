import { useEffect, useState, useCallback } from 'react'
import { RefreshCw, ChevronRight } from 'lucide-react'
import { logsApi } from '../api/client'
import type { AgentRun } from '../api/client'
import { Table, Pagination } from '../components/ui/Table'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Modal } from '../components/ui/Modal'
import { Card } from '../components/ui/Card'

function fmtDt(s?: string) {
  if (!s) return '—'
  return new Date(s).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
}

const NODE_NAMES = ['', 'classify_intent', 'negotiation', 'scheduling', 'rescheduling', 'reply_generation', 'send_reply']
const STATUSES = ['', 'success', 'error', 'running']

export function Logs() {
  const [data, setData] = useState<{ items: AgentRun[]; total: number }>({ items: [], total: 0 })
  const [page, setPage] = useState(1)
  const [nodeFilter, setNodeFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<AgentRun | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    logsApi.list({
      page, page_size: 25,
      node_name: nodeFilter || undefined,
      status: statusFilter || undefined,
    }).then(setData).finally(() => setLoading(false))
  }, [page, nodeFilter, statusFilter])

  useEffect(() => { load() }, [load])
  useEffect(() => { setPage(1) }, [nodeFilter, statusFilter])

  const columns = [
    { key: 'id', header: '#', className: 'w-12', render: (r: AgentRun) => <span className="text-slate-500">#{r.id}</span> },
    { key: 'node_name', header: 'Node', render: (r: AgentRun) => (
      <span className="font-mono text-xs text-indigo-300 bg-indigo-500/10 px-2 py-0.5 rounded">{r.node_name}</span>
    )},
    { key: 'thread_id', header: 'Thread', render: (r: AgentRun) => <span className="text-slate-400">#{r.thread_id}</span> },
    { key: 'status', header: 'Status', render: (r: AgentRun) => <Badge value={r.status} /> },
    { key: 'latency_ms', header: 'Latency', render: (r: AgentRun) => (
      <span className="text-slate-400">{r.latency_ms != null ? `${r.latency_ms} ms` : '—'}</span>
    )},
    { key: 'created_at', header: 'Time', render: (r: AgentRun) => (
      <span className="text-slate-500 text-xs">{fmtDt(r.created_at)}</span>
    )},
    { key: 'detail', header: '', className: 'w-10', render: (r: AgentRun) => (
      <Button size="sm" variant="ghost" onClick={() => setSelected(r)}>
        <ChevronRight size={13} />
      </Button>
    )},
  ]

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-200">Agent Logs</h1>
          <p className="text-sm text-slate-500 mt-0.5">{data.total} records</p>
        </div>
        <Button variant="ghost" size="sm" onClick={load}><RefreshCw size={14} /></Button>
      </div>

      <div className="flex gap-3">
        <select
          value={nodeFilter}
          onChange={e => setNodeFilter(e.target.value)}
          className="px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#1a1d27] text-slate-300 focus:outline-none focus:border-indigo-500"
        >
          {NODE_NAMES.map(n => <option key={n} value={n}>{n || 'All nodes'}</option>)}
        </select>
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#1a1d27] text-slate-300 focus:outline-none focus:border-indigo-500"
        >
          {STATUSES.map(s => <option key={s} value={s}>{s || 'All statuses'}</option>)}
        </select>
      </div>

      <Table
        columns={columns as never} data={data.items as never} loading={loading}
        emptyMessage="No agent runs recorded yet."
        onRowClick={r => setSelected(r as AgentRun)}
      />
      <Pagination page={page} total={data.total} pageSize={25} onChange={setPage} />

      {selected && <LogDetailModal log={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}

function LogDetailModal({ log, onClose }: { log: AgentRun; onClose: () => void }) {
  return (
    <Modal open title={`Log #${log.id} — ${log.node_name}`} onClose={onClose} width="max-w-2xl">
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <Badge value={log.status} />
          <span className="text-xs text-slate-500">Thread #{log.thread_id}</span>
          {log.latency_ms != null && <span className="text-xs text-slate-500">{log.latency_ms} ms</span>}
        </div>

        {log.error_message && (
          <div className="px-4 py-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-300 text-sm font-mono">
            {log.error_message}
          </div>
        )}

        {log.input_payload && (
          <Card title="Input">
            <pre className="text-xs text-slate-400 overflow-auto max-h-40 whitespace-pre-wrap">
              {JSON.stringify(log.input_payload, null, 2)}
            </pre>
          </Card>
        )}

        {log.output_payload && (
          <Card title="Output">
            <pre className="text-xs text-slate-400 overflow-auto max-h-40 whitespace-pre-wrap">
              {JSON.stringify(log.output_payload, null, 2)}
            </pre>
          </Card>
        )}
      </div>
    </Modal>
  )
}
