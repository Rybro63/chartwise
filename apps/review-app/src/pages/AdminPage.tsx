import { useState } from 'react'
import { api, type EncounterStatus } from '../api'
import { StatusBadge } from '../components/StatusBadge'
import { formatTime } from '../lib/format'
import { usePolling } from '../lib/usePolling'

export function AdminPage() {
  const [status, setStatus] = useState<EncounterStatus | ''>('DRAFT_FAILED')
  const [auditFor, setAuditFor] = useState<string>()
  const encounters = usePolling(() => api.adminEncounters(status || undefined), 5000, () => true)
  const audit = usePolling(() => api.audit(auditFor), 0, () => false)
  const [message, setMessage] = useState<string>()

  async function retry(id: string) {
    try {
      await api.retry(id)
      setMessage(`Retry queued for ${id.slice(0, 8)}.`)
      await encounters.refresh()
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e))
    }
  }

  async function showAudit(id?: string) {
    setAuditFor(id)
    audit.setData(await api.audit(id))
  }

  return (
    <main className="page wide">
      <h2>Operations</h2>
      <p className="muted">Operational view. Patient names, transcripts and notes are not shown to admins.</p>
      <div className="page-head">
        <h3>Encounters</h3>
        <select value={status} onChange={(e) => setStatus(e.target.value as EncounterStatus | '')}>
          <option value="DRAFT_FAILED">Draft failed</option>
          <option value="APPROVED">Approved (not yet filed)</option>
          <option value="DRAFTING">Drafting</option>
          <option value="">All</option>
        </select>
      </div>
      {message && <p className="ok">{message}</p>}
      <table className="table">
        <thead>
          <tr><th>Encounter</th><th>Status</th><th>Attempt</th><th>Failure</th><th>Updated</th><th /></tr>
        </thead>
        <tbody>
          {(encounters.data ?? []).map((e) => (
            <tr key={e.id}>
              <td><code>{e.id.slice(0, 8)}</code></td>
              <td><StatusBadge status={e.status} /></td>
              <td>{e.draftAttempt}</td>
              <td className="small">{e.failureReason ?? '—'}</td>
              <td>{formatTime(e.updatedAt)}</td>
              <td className="row-actions">
                {(e.status === 'DRAFT_FAILED' || (e.status === 'APPROVED' && e.failureReason)) && (
                  <button onClick={() => retry(e.id)}>Retry</button>
                )}
                <button className="link" onClick={() => showAudit(e.id)}>Audit</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="page-head">
        <h3>Audit log {auditFor && <span className="muted small">for {auditFor.slice(0, 8)}</span>}</h3>
        {auditFor && <button className="link" onClick={() => showAudit(undefined)}>Show all</button>}
      </div>
      <table className="table">
        <thead>
          <tr><th>#</th><th>When</th><th>Actor</th><th>Action</th><th>Encounter</th><th>Version</th><th>Detail</th></tr>
        </thead>
        <tbody>
          {(audit.data ?? []).map((a) => (
            <tr key={a.id}>
              <td>{a.id}</td>
              <td>{formatTime(a.occurredAt)}</td>
              <td>{a.actor} <span className="muted small">{a.actorRole}</span></td>
              <td><code>{a.action}</code></td>
              <td>{a.encounterId ? <code>{a.encounterId.slice(0, 8)}</code> : '—'}</td>
              <td>{a.noteVersion ?? '—'}</td>
              <td className="small">{a.detail === '{}' ? '' : a.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  )
}
