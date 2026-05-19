import { useEffect, useState } from 'react'
import { Save } from 'lucide-react'
import { configApi } from '../api/client'
import type { AgentConfig } from '../api/client'
import { Card } from '../components/ui/Card'
import { Button } from '../components/ui/Button'

type FormState = Omit<AgentConfig, 'id' | 'created_at' | 'updated_at'>

const TONE_OPTIONS = ['professional', 'friendly', 'formal', 'casual']

export function Config() {
  const [config, setConfig] = useState<AgentConfig | null>(null)
  const [form, setForm] = useState<FormState | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    configApi.get().then(c => {
      setConfig(c)
      setForm({
        gig_description: c.gig_description,
        budget_ceiling: c.budget_ceiling,
        tone: c.tone,
        timezone: c.timezone,
        working_hours_start: c.working_hours_start,
        working_hours_end: c.working_hours_end,
        follow_up_days: c.follow_up_days,
        max_follow_ups: c.max_follow_ups,
        is_active: c.is_active,
      })
    }).finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    if (!form) return
    setSaving(true)
    setError('')
    try {
      const updated = await configApi.update(form)
      setConfig(updated)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (ex: unknown) {
      const detail = (ex as { response?: { data?: { detail?: string } } }).response?.data?.detail
      setError(detail || 'Failed to save config.')
    } finally {
      setSaving(false)
    }
  }

  const set = (key: keyof FormState, value: unknown) =>
    setForm(prev => prev ? { ...prev, [key]: value } : prev)

  if (loading || !form) {
    return <div className="p-6 text-slate-500">Loading config…</div>
  }

  return (
    <div className="p-6 max-w-2xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-200">Agent Config</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Config ID: {config?.id} · Last updated: {config?.updated_at ? new Date(config.updated_at).toLocaleString() : '—'}
          </p>
        </div>
        <Button variant="primary" onClick={handleSave} loading={saving}>
          <Save size={14} /> {saving ? 'Saving…' : 'Save Changes'}
        </Button>
      </div>

      {saved && <div className="px-4 py-2.5 rounded-lg border border-green-500/30 bg-green-500/10 text-green-300 text-sm">Configuration saved successfully.</div>}
      {error && <div className="px-4 py-2.5 rounded-lg border border-red-500/30 bg-red-500/10 text-red-300 text-sm">{error}</div>}

      <Card title="Outreach">
        <div className="space-y-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1.5">Gig / Opportunity Description</label>
            <textarea
              rows={4}
              value={form.gig_description}
              onChange={e => set('gig_description', e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500 resize-none"
              placeholder="Describe the role or opportunity the agent will pitch…"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-slate-400 mb-1.5">Tone</label>
              <select
                value={form.tone}
                onChange={e => set('tone', e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500"
              >
                {TONE_OPTIONS.map(t => <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1.5">Budget Ceiling (USD)</label>
              <input
                type="number" min={0}
                value={form.budget_ceiling}
                onChange={e => set('budget_ceiling', parseFloat(e.target.value))}
                className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500"
              />
            </div>
          </div>
        </div>
      </Card>

      <Card title="Schedule">
        <div className="grid grid-cols-3 gap-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1.5">Timezone</label>
            <input
              type="text"
              value={form.timezone}
              onChange={e => set('timezone', e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1.5">Working Hours Start</label>
            <input
              type="number" min={0} max={23}
              value={form.working_hours_start}
              onChange={e => set('working_hours_start', parseInt(e.target.value))}
              className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1.5">Working Hours End</label>
            <input
              type="number" min={1} max={24}
              value={form.working_hours_end}
              onChange={e => set('working_hours_end', parseInt(e.target.value))}
              className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500"
            />
          </div>
        </div>
      </Card>

      <Card title="Silent Follow-Up">
        <p className="text-xs text-slate-500 mb-4">
          If a prospect hasn't replied after <strong className="text-slate-300">{form.follow_up_days} day(s)</strong>, the agent
          automatically sends a follow-up. After <strong className="text-slate-300">{form.max_follow_ups}</strong> follow-ups with
          no response, the thread is closed.
        </p>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1.5">Days before follow-up</label>
            <input
              type="number" min={1} max={30}
              value={form.follow_up_days}
              onChange={e => set('follow_up_days', parseInt(e.target.value))}
              className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1.5">Max follow-ups per prospect</label>
            <input
              type="number" min={0} max={10}
              value={form.max_follow_ups}
              onChange={e => set('max_follow_ups', parseInt(e.target.value))}
              className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500"
            />
          </div>
        </div>
      </Card>

      <Card title="Status">
        <label className="flex items-center gap-3 cursor-pointer">
          <div
            onClick={() => set('is_active', !form.is_active)}
            className={`relative w-10 h-5 rounded-full transition-colors ${form.is_active ? 'bg-indigo-600' : 'bg-[#2a2d3e]'}`}
          >
            <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${form.is_active ? 'translate-x-5' : 'translate-x-0.5'}`} />
          </div>
          <span className="text-sm text-slate-300">Config Active</span>
          <span className="text-xs text-slate-500">(only one config can be active at a time)</span>
        </label>
      </Card>
    </div>
  )
}
