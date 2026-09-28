import { SOAP_SECTIONS, type SoapNote } from '../api'
import { diffWords, retainedRatio } from '../lib/diff'

export function DiffView({ before, after, beforeLabel, afterLabel }: {
  before: SoapNote
  after: SoapNote
  beforeLabel: string
  afterLabel: string
}) {
  const all = (n: SoapNote) => SOAP_SECTIONS.map((s) => n[s]).join('\n')
  const retained = retainedRatio(all(before), all(after))
  return (
    <div className="diff">
      <p className="muted">
        Changes from {beforeLabel} to {afterLabel}. {Math.round(retained * 100)}% of the words in {afterLabel} came
        from {beforeLabel}. <span className="diff-removed">removed</span> <span className="diff-added">added</span>
      </p>
      {SOAP_SECTIONS.map((section) => (
        <section key={section}>
          <h4>{section}</h4>
          <p className="diff-text">
            {diffWords(before[section], after[section]).map((part, i) => (
              <span key={i} className={part.kind === 'same' ? undefined : `diff-${part.kind}`}>
                {part.text}
              </span>
            ))}
          </p>
        </section>
      ))}
    </div>
  )
}
