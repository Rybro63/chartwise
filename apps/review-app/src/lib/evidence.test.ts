import { describe, expect, it } from 'vitest'
import type { DraftDetail, SoapNote } from '../api'
import { sourceLines } from './evidence'

const detail: DraftDetail = {
  evidence: [
    { section: 'subjective', text: 'Reports morning headaches.', lines: [6], fromRecord: false },
    { section: 'assessment', text: 'Essential hypertension.', lines: [], fromRecord: true },
    { section: 'plan', text: 'Increase lisinopril to 20 mg daily.', lines: [14, 15], fromRecord: false },
    { section: 'plan', text: 'Start aspirin 81 mg.', lines: [], fromRecord: false },
  ],
  medications: [],
}

const note: SoapNote = {
  subjective: 'Reports morning headaches.',
  objective: '',
  assessment: 'Essential hypertension.',
  plan: 'Increase lisinopril to 20 mg daily.\nStart aspirin 81 mg.\nFollow up in four weeks.',
}

describe('sourceLines', () => {
  it('links each draft line to its transcript lines, the record, or nothing', () => {
    expect(sourceLines(note, detail).map((l) => [l.kind, l.lines])).toEqual([
      ['transcript', [6]],
      ['record', []],
      ['transcript', [14, 15]],
      ['unsourced', []],
      ['edited', []],
    ])
  })

  it('does not carry sources over to a line the reviewer changed', () => {
    const edited = { ...note, plan: 'Increase lisinopril to 40 mg daily.' }
    expect(sourceLines(edited, detail).find((l) => l.section === 'plan')?.kind).toBe('edited')
  })

  it('matches within the same section only', () => {
    const moved = { ...note, subjective: '', objective: 'Reports morning headaches.' }
    expect(sourceLines(moved, detail).find((l) => l.section === 'objective')?.kind).toBe('edited')
  })
})
