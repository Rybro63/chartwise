import type { SafetyFlag } from '../api'

export function SafetyFlags({ flags }: { flags: SafetyFlag[] }) {
  if (flags.length === 0) {
    return <div className="flags flags-clear">Safety check: no issues found in the AI draft.</div>
  }
  const high = flags.filter((f) => f.severity === 'high').length
  return (
    <div className={`flags ${high ? 'flags-high' : 'flags-low'}`} role="alert">
      <strong>
        Safety check flagged {flags.length} issue{flags.length === 1 ? '' : 's'} in the AI draft
        {high ? ` (${high} high severity)` : ''}. Verify before approving.
      </strong>
      <ul>
        {flags.map((f, i) => (
          <li key={i}>
            <span className={`sev sev-${f.severity}`}>{f.severity}</span> <code>{f.code}</code> in{' '}
            <em>{f.section}</em>: {f.message}
          </li>
        ))}
      </ul>
    </div>
  )
}
