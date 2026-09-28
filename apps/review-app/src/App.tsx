import { BrowserRouter, Link, Navigate, NavLink, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth'
import { AdminPage } from './pages/AdminPage'
import { EncounterPage } from './pages/EncounterPage'
import { LoginPage } from './pages/LoginPage'
import { NewEncounterPage } from './pages/NewEncounterPage'
import { QueuePage } from './pages/QueuePage'

function Shell() {
  const { session, logout } = useAuth()
  if (!session) return <LoginPage />
  const isAdmin = session.role === 'ADMIN'

  return (
    <>
      <nav className="topbar">
        <Link to="/" className="brand">Chartwise</Link>
        {!isAdmin && <NavLink to="/" end>Queue</NavLink>}
        {!isAdmin && <NavLink to="/new">New visit</NavLink>}
        {isAdmin && <NavLink to="/admin">Operations</NavLink>}
        <span className="spacer" />
        <span className="who">{session.displayName} · {session.role.toLowerCase()}</span>
        <button className="link" onClick={logout}>Sign out</button>
      </nav>
      <Routes>
        {isAdmin ? (
          <>
            <Route path="/admin" element={<AdminPage />} />
            <Route path="*" element={<Navigate to="/admin" replace />} />
          </>
        ) : (
          <>
            <Route path="/" element={<QueuePage />} />
            <Route path="/new" element={<NewEncounterPage />} />
            <Route path="/encounters/:id" element={<EncounterPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </>
        )}
      </Routes>
    </>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </AuthProvider>
  )
}
