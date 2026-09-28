import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, loadSession, saveSession, setUnauthorizedHandler, type Session } from './api'

interface AuthState {
  session: Session | null
  login: (username: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => loadSession())

  const logout = useCallback(() => {
    saveSession(null)
    setSession(null)
  }, [])

  useEffect(() => setUnauthorizedHandler(logout), [logout])

  const login = useCallback(async (username: string, password: string) => {
    const s = await api.login(username, password)
    saveSession(s)
    setSession(s)
  }, [])

  const value = useMemo(() => ({ session, login, logout }), [session, login, logout])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
