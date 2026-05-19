import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Dashboard } from './pages/Dashboard'
import { Prospects } from './pages/Prospects'
import { Threads } from './pages/Threads'
import { Meetings } from './pages/Meetings'
import { Logs } from './pages/Logs'
import { Config } from './pages/Config'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/"          element={<Dashboard />} />
          <Route path="/prospects" element={<Prospects />} />
          <Route path="/threads"   element={<Threads />} />
          <Route path="/meetings"  element={<Meetings />} />
          <Route path="/logs"      element={<Logs />} />
          <Route path="/config"    element={<Config />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
