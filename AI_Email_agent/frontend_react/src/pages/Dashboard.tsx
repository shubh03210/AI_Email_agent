import { useEffect, useState } from 'react'
import { Users, MessageSquare, Calendar, Activity } from 'lucide-react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell
} from 'recharts'
import { prospectsApi, meetingsApi, logsApi } from '../api/client'
import type { Prospect, AgentRun } from '../api/client'
import { MetricCard, Card } from '../components/ui/Card'
import { Badge } from '../components/ui/Badge'

const STATUS_COLORS: Record<string, string> = {
  pending:     '#f59e0b',
  contacted:   '#6366f1',
  interested:  '#22c55e',
  negotiating: '#f97316',
  scheduled:   '#06b6d4',
  declined:    '#ef4444',
  closed:      '#6b7280',
}

function fmtDt(s?: string) {
  if (!s) return '—'
  return new Date(s).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
}

export function Dashboard() {
  const [prospects, setProspects] = useState<Prospect[]>([])
  const [meetings, setMeetings] = useState<{ total: number }>({ total: 0 })
  const [logs, setLogs] = useState<AgentRun[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      prospectsApi.list({ page_size: 100 }),
      meetingsApi.list({ page_size: 1 }),
      logsApi.list({ page_size: 15 }),
    ]).then(([p, m, l]) => {
      setProspects(p.items)
      setMeetings({ total: m.total })
      setLogs(l.items)
    }).finally(() => setLoading(false))
  }, [])

  const statusCounts = prospects.reduce<Record<string, number>>((acc, p) => {
    acc[p.status] = (acc[p.status] || 0) + 1
    return acc
  }, {})

  const chartData = Object.entries(statusCounts).map(([status, count]) => ({ status, count }))

  const contacted = statusCounts['contacted'] || 0
  const scheduled = statusCounts['scheduled'] || 0

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-xl font-bold text-slate-200">Dashboard</h1>
        <p className="text-sm text-slate-500 mt-0.5">AI Email Agent overview</p>
      </div>

      {/* Metrics */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          label="Total Prospects"
          value={loading ? '…' : prospects.length}
          icon={<Users size={18} />}
          color="text-indigo-400"
        />
        <MetricCard
          label="Contacted"
          value={loading ? '…' : contacted}
          sub="awaiting reply"
          icon={<MessageSquare size={18} />}
          color="text-blue-400"
        />
        <MetricCard
          label="Meetings Booked"
          value={loading ? '…' : meetings.total}
          icon={<Calendar size={18} />}
          color="text-cyan-400"
        />
        <MetricCard
          label="Calls Scheduled"
          value={loading ? '…' : scheduled}
          icon={<Activity size={18} />}
          color="text-green-400"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Pipeline Chart */}
        <Card title="Prospect Pipeline">
          {loading ? (
            <div className="h-48 flex items-center justify-center text-slate-500">Loading…</div>
          ) : chartData.length === 0 ? (
            <div className="h-48 flex items-center justify-center text-slate-500">No prospects yet</div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={chartData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                <XAxis dataKey="status" tick={{ fill: '#64748b', fontSize: 11 }} />
                <YAxis allowDecimals={false} tick={{ fill: '#64748b', fontSize: 11 }} />
                <Tooltip
                  contentStyle={{ background: '#1a1d27', border: '1px solid #2a2d3e', borderRadius: 8 }}
                  labelStyle={{ color: '#e2e8f0' }}
                  cursor={{ fill: '#20243a' }}
                />
                <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                  {chartData.map((entry) => (
                    <Cell key={entry.status} fill={STATUS_COLORS[entry.status] || '#6366f1'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>

        {/* Recent Agent Activity */}
        <Card title="Recent Agent Activity">
          {loading ? (
            <div className="h-48 flex items-center justify-center text-slate-500">Loading…</div>
          ) : logs.length === 0 ? (
            <div className="h-48 flex items-center justify-center text-slate-500 text-sm">
              No agent runs yet
            </div>
          ) : (
            <div className="space-y-2 max-h-52 overflow-y-auto pr-1">
              {logs.map(log => (
                <div key={log.id} className="flex items-center justify-between py-2 border-b border-[#2a2d3e] last:border-0">
                  <div>
                    <p className="text-sm text-slate-300 font-medium">{log.node_name}</p>
                    <p className="text-xs text-slate-500">Thread #{log.thread_id} · {fmtDt(log.created_at)}</p>
                  </div>
                  <div className="flex items-center gap-2 text-right">
                    <Badge value={log.status} />
                    <span className="text-xs text-slate-500">{log.latency_ms ?? '—'} ms</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
