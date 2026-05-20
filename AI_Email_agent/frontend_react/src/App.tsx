import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { Layout } from './components/Layout'
import { Login } from './pages/Login'
import { Dashboard } from './pages/Dashboard'
import { Prospects } from './pages/Prospects'
import { Threads } from './pages/Threads'
import { Meetings } from './pages/Meetings'
import { Logs } from './pages/Logs'
import { Config } from './pages/Config'

function ProtectedApp() {
  const { isAuthenticated } = useAuth()

  if (!isAuthenticated) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }

  return (
    <Routes>
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route element={<Layout />}>
        <Route path="/"          element={<Dashboard />} />
        <Route path="/prospects" element={<Prospects />} />
        <Route path="/threads"   element={<Threads />} />
        <Route path="/meetings"  element={<Meetings />} />
        <Route path="/logs"      element={<Logs />} />
        <Route path="/config"    element={<Config />} />
        <Route path="*"          element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <ProtectedApp />
      </BrowserRouter>
    </AuthProvider>
  )
}
