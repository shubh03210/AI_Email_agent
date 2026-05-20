import { type FormEvent, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bot, Eye, EyeOff } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'

export function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await login(username, password)
      navigate('/', { replace: true })
    } catch {
      setError('Invalid username or password.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#0f1117] px-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-12 h-12 rounded-xl bg-indigo-600 flex items-center justify-center mb-4 shadow-lg shadow-indigo-900/40">
            <Bot size={24} className="text-white" />
          </div>
          <h1 className="text-xl font-bold text-slate-200">AI Email Agent</h1>
          <p className="text-sm text-slate-500 mt-1">Sign in to your dashboard</p>
        </div>

        {/* Card */}
        <form
          onSubmit={handleSubmit}
          className="bg-[#1a1d27] border border-[#2a2d3e] rounded-xl p-6 space-y-4 shadow-xl"
        >
          {error && (
            <div className="text-sm text-red-400 bg-red-900/20 border border-red-900/40 rounded-lg px-3 py-2">
              {error}
            </div>
          )}

          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              Username
            </label>
            <input
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              className="w-full bg-[#0f1117] border border-[#2a2d3e] rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500 transition-colors"
              placeholder="admin"
              required
              autoComplete="username"
              autoFocus
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              Password
            </label>
            <div className="relative">
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={e => setPassword(e.target.value)}
                className="w-full bg-[#0f1117] border border-[#2a2d3e] rounded-lg px-3 py-2 pr-10 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500 transition-colors"
                placeholder="••••••••"
                required
                autoComplete="current-password"
              />
              <button
                type="button"
                onClick={() => setShowPassword(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors"
                tabIndex={-1}
              >
                {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg px-4 py-2.5 transition-colors mt-2"
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
