import { SOAP_SECTIONS, type DraftDetail, type EvidenceStatement, type SoapNote } from '../api'

export type SourceKind = 'transcript' | 'record' | 'absence' | 'unsourced' | 'edited'

export interface SourcedLine {
  section: keyof SoapNote
  text: string
  kind: SourceKind
  /** Transcript lines (1-based) this line came from; empty unless kind is 'transcript'. */
  lines: number[]
}

/**
 * Matches each line of the note being reviewed to the AI draft statement it came from. A line
 * the reviewer has changed no longer matches, so it is shown as edited rather than borrowing the
 * old statement's sources.
 */
export function sourceLines(note: SoapNote, detail: DraftDetail): SourcedLine[] {
  const bySection = new Map<string, EvidenceStatement[]>()
  for (const s of detail.evidence) {
    const key = `${s.section}\u0000${s.text.trim()}`
    bySection.set(key, [...(bySection.get(key) ?? []), s])
  }
  const out: SourcedLine[] = []
  for (const section of SOAP_SECTIONS) {
    for (const raw of note[section].split('\n')) {
      const text = raw.trim()
      if (!text) continue
      const statement = bySection.get(`${section}\u0000${text}`)?.[0]
      if (!statement) out.push({ section, text, kind: 'edited', lines: [] })
      else if (statement.lines.length) out.push({ section, text, kind: 'transcript', lines: statement.lines })
      else out.push({ section, text, kind: statement.fromRecord ? 'record' : statement.absence ? 'absence' : 'unsourced', lines: [] })
    }
  }
  return out
}
