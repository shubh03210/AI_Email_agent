import { useEffect, useState } from 'react'
import { Users, MessageSquare, Calendar, Activity, TrendingUp, RefreshCw, AlertTriangle } from 'lucide-react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell
} from 'recharts'
import { metricsApi, logsApi } from '../api/client'
import type { Metrics, AgentRun } from '../api/client'
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

function pct(rate: number) {
  return `${(rate * 100).toFixed(1)}%`
}

export function Dashboard() {
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [logs, setLogs] = useState<AgentRun[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    Promise.all([
      metricsApi.get(),
      logsApi.list({ page_size: 15 }),
    ]).then(([m, l]) => {
      setMetrics(m)
      setLogs(l.items)
    }).catch(() => setError('Failed to load dashboard data.')).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const chartData = metrics
    ? Object.entries(metrics.pipeline).map(([status, count]) => ({ status, count }))
    : []

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-200">Dashboard</h1>
          <p className="text-sm text-slate-500 mt-0.5">AI Email Agent — pipeline overview</p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-slate-400 border border-[#2a2d3e] rounded-lg hover:border-indigo-500/50 hover:text-indigo-400 transition-colors disabled:opacity-40"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 px-4 py-2.5 rounded-lg border border-red-500/30 bg-red-500/10 text-red-300 text-sm">
          <AlertTriangle size={14} />
          {error}
        </div>
      )}

      {/* Row 1 — Volume metrics */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          label="Total Prospects"
          value={loading ? '…' : metrics?.total_prospects ?? 0}
          icon={<Users size={18} />}
          color="text-indigo-400"
        />
        <MetricCard
          label="Outreach Sent"
          value={loading ? '…' : metrics?.outreach_sent ?? 0}
          sub="left pending"
          icon={<MessageSquare size={18} />}
          color="text-blue-400"
        />
        <MetricCard
          label="Meetings Booked"
          value={loading ? '…' : metrics?.meetings_booked ?? 0}
          icon={<Calendar size={18} />}
          color="text-cyan-400"
        />
        <MetricCard
          label="Active Negotiations"
          value={loading ? '…' : metrics?.active_negotiations ?? 0}
          icon={<Activity size={18} />}
          color="text-orange-400"
        />
      </div>

      {/* Row 2 — Rate metrics */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          label="Response Rate"
          value={loading ? '…' : pct(metrics?.response_rate ?? 0)}
          sub="of outreach sent"
          icon={<TrendingUp size={18} />}
          color="text-green-400"
        />
        <MetricCard
          label="Booking Conversion"
          value={loading ? '…' : pct(metrics?.booking_conversion ?? 0)}
          sub="meetings / outreach"
          icon={<Calendar size={18} />}
          color="text-teal-400"
        />
        <MetricCard
          label="Negotiation Success"
          value={loading ? '…' : pct(metrics?.negotiation_success ?? 0)}
          sub="accepted of resolved"
          icon={<TrendingUp size={18} />}
          color="text-emerald-400"
        />
        <MetricCard
          label="Reschedule Rate"
          value={loading ? '…' : pct(metrics?.reschedule_pct ?? 0)}
          sub={`walkaway: ${loading ? '…' : pct(metrics?.walkaway_pct ?? 0)}`}
          icon={<RefreshCw size={18} />}
          color="text-yellow-400"
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
