import { useEffect, useState } from 'react'
import { Save } from 'lucide-react'
import { configApi } from '../api/client'
import type { AgentConfig } from '../api/client'
import { Card } from '../components/ui/Card'
import { Button } from '../components/ui/Button'

type FormState = Omit<AgentConfig, 'id' | 'created_at' | 'updated_at'>

const TONE_OPTIONS = [
  { value: 'formal',       label: 'Formal',       desc: 'Authoritative, structured, no contractions' },
  { value: 'friendly',     label: 'Friendly',      desc: 'Warm, approachable, conversational' },
  { value: 'startup',      label: 'Startup',       desc: 'Energetic, direct, growth-oriented' },
  { value: 'executive',    label: 'Executive',     desc: 'Concise, high-impact, results-oriented' },
  { value: 'professional', label: 'Professional',  desc: 'Polished, balanced, universally appropriate' },
  { value: 'casual',       label: 'Casual',        desc: 'Relaxed, informal, low-pressure' },
]

const CADENCE_PRESETS = [
  { label: 'Day 1 / 3 / 7',        value: '[1, 3, 7]' },
  { label: 'Day 2 / 5 / 10 / 14',  value: '[2, 5, 10, 14]' },
  { label: 'Day 1 / 3 / 7 / 14',   value: '[1, 3, 7, 14]' },
  { label: 'Daily × 3',            value: '[1, 1, 1]' },
]

function parseCadence(raw: string): number[] {
  try {
    const arr = JSON.parse(raw)
    if (Array.isArray(arr) && arr.every(n => typeof n === 'number' && n > 0)) return arr
  } catch (_) { /* noop */ }
  return [1, 3, 7]
}

function formatCadenceLabel(raw: string): string {
  const arr = parseCadence(raw)
  return arr.map((d, i) => `Day ${arr.slice(0, i + 1).reduce((a, b) => a + b, 0)}`).join(' → ')
}

export function Config() {
  const [config, setConfig] = useState<AgentConfig | null>(null)
  const [form, setForm] = useState<FormState | null>(null)
  const [cadenceInput, setCadenceInput] = useState('')
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
        // follow_up_days and max_follow_ups are superseded by follow_up_cadence;
        // keep them in state for schema compatibility but do not send on save.
        follow_up_days: c.follow_up_days,
        max_follow_ups: c.max_follow_ups,
        is_active: c.is_active,
        follow_up_cadence: c.follow_up_cadence ?? '[1, 3, 7]',
        recruiter_name: c.recruiter_name ?? 'Alex',
        recruiter_title: c.recruiter_title ?? 'HR Recruiter',
        recruiter_signature: c.recruiter_signature ?? '',
        meeting_confirmation_template: c.meeting_confirmation_template ?? '',
      })
      setCadenceInput(c.follow_up_cadence ?? '[1, 3, 7]')
    }).finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    if (!form) return
    setSaving(true)
    setError('')
    try {
      // Exclude stale follow_up_days/max_follow_ups — superseded by follow_up_cadence.
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const { follow_up_days: _fd, max_follow_ups: _mfu, ...saveableForm } = form
      const updated = await configApi.update({ ...saveableForm, follow_up_cadence: cadenceInput })
      setConfig(updated)
      setForm(prev => prev ? { ...prev, follow_up_cadence: updated.follow_up_cadence } : prev)
      setCadenceInput(updated.follow_up_cadence)
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

      {/* Outreach */}
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
              <label className="block text-xs text-slate-400 mb-1.5">Tone Preset</label>
              <select
                value={form.tone}
                onChange={e => set('tone', e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500"
              >
                {TONE_OPTIONS.map(t => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
              <p className="text-xs text-slate-500 mt-1">
                {TONE_OPTIONS.find(t => t.value === form.tone)?.desc ?? ''}
              </p>
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1.5">Budget Ceiling (USD)</label>
              <input
                type="number" min={0}
                value={form.budget_ceiling}
                onChange={e => set('budget_ceiling', parseFloat(e.target.value) || 0)}
                className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500"
              />
            </div>
          </div>
        </div>
      </Card>

      {/* Recruiter Identity */}
      <Card title="Recruiter Identity">
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-slate-400 mb-1.5">Recruiter Name</label>
              <input
                type="text"
                value={form.recruiter_name}
                onChange={e => set('recruiter_name', e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500"
                placeholder="Alex"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1.5">Recruiter Title</label>
              <input
                type="text"
                value={form.recruiter_title}
                onChange={e => set('recruiter_title', e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500"
                placeholder="HR Recruiter"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1.5">Email Signature</label>
            <textarea
              rows={4}
              value={form.recruiter_signature}
              onChange={e => set('recruiter_signature', e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500 resize-none font-mono text-xs"
              placeholder={"Best regards,\nAlex Chen\nHR Recruiter | TechCorp\n+1 (555) 123-4567"}
            />
            <p className="text-xs text-slate-500 mt-1">Appended to every outbound email after the LLM-generated body.</p>
          </div>
        </div>
      </Card>

      {/* Meeting Confirmation Template */}
      <Card title="Meeting Confirmation Template">
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Template</label>
          <textarea
            rows={7}
            value={form.meeting_confirmation_template}
            onChange={e => set('meeting_confirmation_template', e.target.value)}
            className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500 resize-none font-mono text-xs"
          />
          <p className="text-xs text-slate-500 mt-1">
            Placeholders: <code className="text-indigo-400">{'{prospect_name}'}</code>{' '}
            <code className="text-indigo-400">{'{meeting_date}'}</code>{' '}
            <code className="text-indigo-400">{'{meeting_time}'}</code>{' '}
            <code className="text-indigo-400">{'{timezone}'}</code>{' '}
            <code className="text-indigo-400">{'{recruiter_name}'}</code>{' '}
            <code className="text-indigo-400">{'{recruiter_title}'}</code>
          </p>
        </div>
      </Card>

      {/* Follow-up Cadence */}
      <Card title="Follow-Up Cadence">
        <div className="space-y-4">
          <p className="text-xs text-slate-500">
            Configure how many days after the <strong className="text-slate-300">previous contact</strong> each
            follow-up is sent. The cadence length determines the maximum number of follow-ups.
          </p>

          {/* Preset buttons */}
          <div>
            <label className="block text-xs text-slate-400 mb-2">Quick presets</label>
            <div className="flex flex-wrap gap-2">
              {CADENCE_PRESETS.map(p => (
                <button
                  key={p.value}
                  onClick={() => setCadenceInput(p.value)}
                  className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
                    cadenceInput === p.value
                      ? 'border-indigo-500 bg-indigo-500/10 text-indigo-300'
                      : 'border-[#2a2d3e] text-slate-400 hover:border-indigo-500/50'
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          {/* Raw JSON input */}
          <div>
            <label className="block text-xs text-slate-400 mb-1.5">Cadence (JSON array of day offsets)</label>
            <input
              type="text"
              value={cadenceInput}
              onChange={e => setCadenceInput(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500 font-mono"
              placeholder="[1, 3, 7]"
            />
            <p className="text-xs text-slate-500 mt-1">
              Current schedule: <span className="text-slate-300">{formatCadenceLabel(cadenceInput)}</span>
              {' · '}{parseCadence(cadenceInput).length} follow-up(s) total
            </p>
          </div>
        </div>
      </Card>

      {/* Schedule */}
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
              onChange={e => set('working_hours_start', parseInt(e.target.value) || 0)}
              className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1.5">Working Hours End</label>
            <input
              type="number" min={1} max={24}
              value={form.working_hours_end}
              onChange={e => set('working_hours_end', parseInt(e.target.value) || 9)}
              className="w-full px-3 py-2 text-sm rounded-lg border border-[#2a2d3e] bg-[#0f1117] text-slate-200 focus:outline-none focus:border-indigo-500"
            />
          </div>
        </div>
      </Card>

      {/* Status */}
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
