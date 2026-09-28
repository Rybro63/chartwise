import { SOAP_SECTIONS, type SoapNote } from '../api'

const LABELS: Record<keyof SoapNote, string> = {
  subjective: 'Subjective',
  objective: 'Objective',
  assessment: 'Assessment',
  plan: 'Plan',
}

export function SoapEditor({ value, onChange, readOnly }: {
  value: SoapNote
  onChange: (next: SoapNote) => void
  readOnly: boolean
}) {
  return (
    <div className="soap">
      {SOAP_SECTIONS.map((section) => (
        <label key={section} className="soap-section">
          <span>{LABELS[section]}</span>
          <textarea
            value={value[section]}
            readOnly={readOnly}
            rows={Math.min(14, Math.max(4, value[section].split('\n').length + 2))}
            onChange={(e) => onChange({ ...value, [section]: e.target.value })}
          />
        </label>
      ))}
    </div>
  )
}
