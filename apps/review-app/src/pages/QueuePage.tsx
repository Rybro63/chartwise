import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type EncounterStatus } from '../api'
import { StatusBadge } from '../components/StatusBadge'
import { elapsed, formatTime } from '../lib/format'
import { usePolling } from '../lib/usePolling'

const FILTERS: { label: string; status?: EncounterStatus }[] = [
  { label: 'Needs review', status: 'IN_REVIEW' },
  { label: 'Drafting', status: 'DRAFTING' },
  { label: 'Filed', status: 'FILED' },
  { label: 'All' },
]

export function QueuePage() {
  const [filter, setFilter] = useState(0)
  const status = FILTERS[filter].status
  const { data, error } = usePolling(() => api.encounters(status), 4000, () => true)

  return (
    <main className="page">
      <div className="page-head">
        <h2>Review queue</h2>
        <Link to="/new" className="button primary">New visit</Link>
      </div>
      <div className="tabs" role="tablist">
        {FILTERS.map((f, i) => (
          <button key={f.label} role="tab" aria-selected={i === filter} className={i === filter ? 'tab active' : 'tab'}
                  onClick={() => setFilter(i)}>
            {f.label}
          </button>
        ))}
      </div>
      {error && <p className="error">{error}</p>}
      {!data ? (
        <p className="muted">Loading…</p>
      ) : data.length === 0 ? (
        <p className="empty">Nothing here.</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Patient</th>
              <th>Status</th>
              <th>Safety flags</th>
              <th>Version</th>
              <th>Recorded</th>
              <th>Draft time</th>
              <th>By</th>
            </tr>
          </thead>
          <tbody>
            {data.map((e) => (
              <tr key={e.id}>
                <td><Link to={`/encounters/${e.id}`}>{e.patientName}</Link></td>
                <td><StatusBadge status={e.status} /></td>
                <td>{e.draftedAt ? (e.safetyFlagCount ? <span className="flag-count">{e.safetyFlagCount}</span> : '0') : '—'}</td>
                <td>{e.currentVersion ? `v${e.currentVersion}` : '—'}</td>
                <td>{formatTime(e.createdAt)}</td>
                <td>{elapsed(e.createdAt, e.draftedAt)}</td>
                <td>{e.createdBy}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  )
}
