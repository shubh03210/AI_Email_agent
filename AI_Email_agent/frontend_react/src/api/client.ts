import axios from 'axios'
import { TOKEN_KEY } from '../contexts/AuthContext'

const api = axios.create({
  baseURL: '/api/v1',
  headers: { 'Content-Type': 'application/json' },
  timeout: 60000,
})

// Inject Bearer token on every request (skipped automatically when token absent)
api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// On 401, clear the stale token and redirect to login
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      // Use replace so the back-button doesn't loop back to the 401 page
      window.location.replace('/login')
    }
    return Promise.reject(error)
  },
)

export default api

// ── Types ─────────────────────────────────────────────────────────────────────

export interface Prospect {
  id: number
  name: string
  email: string
  company?: string
  timezone: string
  status: string
  created_at: string
  updated_at: string
}

export interface ProspectCreate {
  name: string
  email: string
  company?: string
  timezone?: string
}

export interface ProspectUpdate {
  name?: string
  email?: string
  company?: string
  timezone?: string
  status?: string
}

export interface EmailMessage {
  id: number
  thread_id: number
  sender: string
  body: string
  timestamp: string
  raw_payload?: Record<string, unknown>
}

export interface EmailThread {
  id: number
  gmail_thread_id: string
  prospect_id: number
  subject: string
  status: string
  follow_up_count: number
  last_outreach_at?: string
  created_at: string
  updated_at: string
  messages?: EmailMessage[]
}

export interface Meeting {
  id: number
  thread_id: number
  prospect_id?: number
  google_event_id?: string
  scheduled_at?: string
  status: string
  created_at: string
  updated_at: string
}

export interface AgentRun {
  id: number
  thread_id: number
  node_name: string
  status: string
  input_payload?: Record<string, unknown>
  output_payload?: Record<string, unknown>
  latency_ms?: number
  error_message?: string
  created_at: string
}

export interface AgentConfig {
  id: number
  gig_description: string
  budget_ceiling: number
  tone: string
  timezone: string
  working_hours_start: number
  working_hours_end: number
  follow_up_days: number
  max_follow_ups: number
  is_active: boolean
  // Phase 8 fields
  follow_up_cadence: string        // JSON array string, e.g. "[1, 3, 7]"
  recruiter_name: string
  recruiter_title: string
  recruiter_signature: string
  meeting_confirmation_template: string
  created_at: string
  updated_at: string
}

export interface Metrics {
  total_prospects: number
  outreach_sent: number
  responses_received: number
  response_rate: number             // 0–1
  meetings_booked: number
  booking_conversion: number        // 0–1
  active_negotiations: number
  negotiations_resolved: number
  negotiation_success: number       // 0–1
  reschedule_pct: number            // 0–1
  walkaway_pct: number              // 0–1
  pipeline: Record<string, number>
  meetings_by_status: Record<string, number>
}

export interface Paginated<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

// ── Prospects ─────────────────────────────────────────────────────────────────

export const prospectsApi = {
  list: (params?: { status?: string; search?: string; page?: number; page_size?: number }) =>
    api.get<Paginated<Prospect>>('/prospects/', { params }).then(r => r.data),

  get: (id: number) =>
    api.get<Prospect>(`/prospects/${id}`).then(r => r.data),

  create: (data: ProspectCreate) =>
    api.post<Prospect>('/prospects/', data).then(r => r.data),

  update: (id: number, data: ProspectUpdate) =>
    api.put<Prospect>(`/prospects/${id}`, data).then(r => r.data),

  delete: (id: number) =>
    api.delete(`/prospects/${id}`),

  triggerOutreach: (id: number) =>
    api.post<{ task_id: string; status: string; message: string }>(`/prospects/${id}/outreach`).then(r => r.data),
}

// ── Threads ───────────────────────────────────────────────────────────────────

export const threadsApi = {
  list: (params?: { prospect_id?: number; status?: string; page?: number; page_size?: number }) =>
    api.get<Paginated<EmailThread>>('/threads/', { params }).then(r => r.data),

  get: (id: number) =>
    api.get<EmailThread>(`/threads/${id}`).then(r => r.data),

  messages: (id: number, params?: { page?: number; page_size?: number }) =>
    api.get<{ items: EmailMessage[]; total: number }>(`/threads/${id}/messages`, { params }).then(r => r.data),

  runAgent: (id: number) =>
    api.post<{ task_id: string; status: string; message: string }>(`/threads/${id}/run`).then(r => r.data),

  logs: (id: number, params?: { page?: number; page_size?: number }) =>
    api.get<Paginated<AgentRun>>(`/threads/${id}/logs`, { params }).then(r => r.data),
}

// ── Meetings ──────────────────────────────────────────────────────────────────

export const meetingsApi = {
  list: (params?: { status?: string; page?: number; page_size?: number }) =>
    api.get<Paginated<Meeting>>('/meetings/', { params }).then(r => r.data),

  get: (id: number) =>
    api.get<Meeting>(`/meetings/${id}`).then(r => r.data),

  cancel: (id: number) =>
    api.post<Meeting>(`/meetings/${id}/cancel`).then(r => r.data),

  reschedule: (id: number) =>
    api.post<{ task_id: string; status: string; message: string }>(`/meetings/${id}/reschedule`).then(r => r.data),
}

// ── Logs ──────────────────────────────────────────────────────────────────────

export const logsApi = {
  list: (params?: { thread_id?: number; node_name?: string; status?: string; page?: number; page_size?: number }) =>
    api.get<Paginated<AgentRun>>('/logs/', { params }).then(r => r.data),

  get: (id: number) =>
    api.get<AgentRun>(`/logs/${id}`).then(r => r.data),
}

// ── Config ────────────────────────────────────────────────────────────────────

export const configApi = {
  get: () =>
    api.get<AgentConfig>('/config/').then(r => r.data),

  update: (data: Partial<AgentConfig>) =>
    api.put<AgentConfig>('/config/', data).then(r => r.data),
}

// ── Metrics ───────────────────────────────────────────────────────────────────

export const metricsApi = {
  get: () =>
    api.get<Metrics>('/metrics/').then(r => r.data),
}

// ── Health ────────────────────────────────────────────────────────────────────

export const healthApi = {
  check: () =>
    api.get<{ status: string; version: string; db: string }>('/health/').then(r => r.data),
}
