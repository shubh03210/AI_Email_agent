import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from 'react'
import api from '../api/client'

export const TOKEN_KEY = 'ai_email_agent_token'
const ROLE_KEY = 'ai_email_agent_role'

export type UserRole = 'admin' | 'operator'

interface TokenApiResponse {
  access_token: string
  token_type: string
  expires_in: number
  role: UserRole
}

interface AuthContextValue {
  isAuthenticated: boolean
  role: UserRole | null
  isAdmin: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

function readStoredRole(): UserRole | null {
  const v = localStorage.getItem(ROLE_KEY)
  return v === 'admin' || v === 'operator' ? v : null
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(
    () => !!localStorage.getItem(TOKEN_KEY),
  )
  const [role, setRole] = useState<UserRole | null>(readStoredRole)

  const login = useCallback(async (username: string, password: string) => {
    const form = new URLSearchParams({ username, password })
    const { data } = await api.post<TokenApiResponse>(
      '/auth/token',
      form,
      { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } },
    )
    localStorage.setItem(TOKEN_KEY, data.access_token)
    localStorage.setItem(ROLE_KEY, data.role)
    setRole(data.role)
    setIsAuthenticated(true)
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(ROLE_KEY)
    setRole(null)
    setIsAuthenticated(false)
  }, [])

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated,
        role,
        isAdmin: role === 'admin',
        login,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
