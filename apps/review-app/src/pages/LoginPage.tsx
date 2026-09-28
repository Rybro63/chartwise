import { useState, type FormEvent } from 'react'
import { ApiError } from '../api'
import { useAuth } from '../auth'

export function LoginPage() {
  const { login } = useAuth()
  const [username, setUsername] = useState('clinician')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string>()
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(undefined)
    try {
      await login(username, password)
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? 'Incorrect username or password.'
          : 'Could not reach the Chartwise server. Is the stack running (make up)?',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login">
      <form onSubmit={submit} className="card">
        <h1>Chartwise</h1>
        <p className="muted">Clinical note review. All patient data in this system is synthetic.</p>
        <label>
          Username
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        <p className="muted small">
          Demo accounts: <strong>clinician</strong> (approves notes), <strong>scribe</strong>,{' '}
          <strong>admin</strong>. Password for all: <code>chartwise-dev</code>
        </p>
      </form>
    </main>
  )
}
