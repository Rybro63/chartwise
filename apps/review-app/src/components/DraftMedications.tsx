import type { DraftMedication } from '../api'

/** The model's own list of the medications in its draft, with the status and dose it understood. */
export function DraftMedications({ medications }: { medications: DraftMedication[] }) {
  if (medications.length === 0) return null
  return (
    <details className="draft-meds">
      <summary>Medications in the AI draft ({medications.length})</summary>
      <table className="table compact">
        <thead>
          <tr><th>Medication</th><th>Status</th><th>Dose stated in visit</th></tr>
        </thead>
        <tbody>
          {medications.map((m, i) => (
            <tr key={i}>
              <td>{m.name}</td>
              <td><span className={`med-status med-${m.status}`}>{m.status}</span></td>
              <td>{m.dose || <span className="muted">not stated</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  )
}
