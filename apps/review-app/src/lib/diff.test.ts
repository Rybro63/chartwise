import { describe, expect, it } from 'vitest'
import { diffWords, retainedRatio } from './diff'

const join = (parts: ReturnType<typeof diffWords>, kinds: string[]) =>
  parts.filter((p) => kinds.includes(p.kind)).map((p) => p.text).join('')

describe('diffWords', () => {
  it('returns a single unchanged part for identical text', () => {
    expect(diffWords('Continue lisinopril 10 mg.', 'Continue lisinopril 10 mg.')).toEqual([
      { kind: 'same', text: 'Continue lisinopril 10 mg.' },
    ])
  })

  it('marks replaced words', () => {
    const parts = diffWords('Continue lisinopril 10 mg daily.', 'Continue lisinopril 20 mg daily.')
    expect(parts.filter((p) => p.kind === 'removed').map((p) => p.text)).toEqual(['10'])
    expect(parts.filter((p) => p.kind === 'added').map((p) => p.text)).toEqual(['20'])
  })

  it('reconstructs both texts exactly', () => {
    const before = 'Start warfarin 5 mg.\nFollow up in 4 weeks.'
    const after = 'Follow up in 2 weeks.\nRecheck BP.'
    const parts = diffWords(before, after)
    expect(join(parts, ['same', 'removed'])).toBe(before)
    expect(join(parts, ['same', 'added'])).toBe(after)
  })

  it('handles empty inputs', () => {
    expect(diffWords('', 'new text')).toEqual([{ kind: 'added', text: 'new text' }])
    expect(diffWords('old text', '')).toEqual([{ kind: 'removed', text: 'old text' }])
  })
})

describe('retainedRatio', () => {
  it('is 1 when the draft is accepted unchanged', () => {
    expect(retainedRatio('a b c d', 'a b c d')).toBe(1)
  })

  it('is the share of final words that came from the draft', () => {
    expect(retainedRatio('a b c d', 'a b c d e f g h')).toBe(0.5)
  })
})
