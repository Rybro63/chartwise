import type { SoapNote } from '../api'
import type { SourcedLine } from '../lib/evidence'

const LABELS: Record<keyof SoapNote, string> = {
  subjective: 'Subjective',
  objective: 'Objective',
  assessment: 'Assessment',
  plan: 'Plan',
}

function describe(line: SourcedLine): string {
  switch (line.kind) {
    case 'transcript':
      return `From transcript line${line.lines.length > 1 ? 's' : ''} ${line.lines.join(', ')}`
    case 'record':
      return 'From the patient record'
    case 'absence':
      return 'Says something was not documented'
    case 'unsourced':
      return 'No source in the transcript or record. Verify before approving.'
    case 'edited':
      return 'Changed since the AI draft'
  }
}

/** The note line by line; selecting a line highlights the transcript lines it came from. */
export function SourcesView({ lines, selected, onSelect }: {
  lines: SourcedLine[]
  selected: SourcedLine | undefined
  onSelect: (line: SourcedLine) => void
}) {
  const unsourced = lines.filter((l) => l.kind === 'unsourced').length
  return (
    <div className="sources">
      <p className="muted small">
        Select a line to see where it came from.{' '}
        {unsourced > 0 && (
          <span className="source-warning">{unsourced === 1 ? '1 line cites' : `${unsourced} lines cite`} no source.</span>
        )}
      </p>
      {(Object.keys(LABELS) as (keyof SoapNote)[]).map((section) => {
        const own = lines.filter((l) => l.section === section)
        return (
          <section key={section} className="sources-section">
            <h4>{LABELS[section]}</h4>
            {own.length === 0 && <p className="muted small">Empty</p>}
            {own.map((line, i) => (
              <button
                key={i}
                type="button"
                className={`source-line source-${line.kind}${selected === line ? ' selected' : ''}`}
                title={describe(line)}
                aria-pressed={selected === line}
                onClick={() => onSelect(line)}
              >
                {line.text}
                <span className="source-tag">{describe(line)}</span>
              </button>
            ))}
          </section>
        )
      })}
    </div>
  )
}
