// Word-level diff (longest common subsequence), used to show what the clinician changed in the
// AI draft. Whitespace runs are tokens too, so the output reconstructs both texts exactly.

export interface DiffPart {
  kind: 'same' | 'added' | 'removed'
  text: string
}

const MAX_CELLS = 4_000_000

export function tokenize(text: string): string[] {
  return text.split(/(\s+)/).filter((t) => t.length > 0)
}

export function diffWords(before: string, after: string): DiffPart[] {
  const a = tokenize(before)
  const b = tokenize(after)
  if (a.length * b.length > MAX_CELLS) {
    // Too large for a word-level table; fall back to whole-text replacement.
    return merge([
      ...(before ? [{ kind: 'removed' as const, text: before }] : []),
      ...(after ? [{ kind: 'added' as const, text: after }] : []),
    ])
  }

  // lcs[i][j] = length of the LCS of a[i..] and b[j..]
  const lcs: Uint32Array[] = Array.from({ length: a.length + 1 }, () => new Uint32Array(b.length + 1))
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1])
    }
  }

  const parts: DiffPart[] = []
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      parts.push({ kind: 'same', text: a[i] })
      i++
      j++
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      parts.push({ kind: 'removed', text: a[i++] })
    } else {
      parts.push({ kind: 'added', text: b[j++] })
    }
  }
  while (i < a.length) parts.push({ kind: 'removed', text: a[i++] })
  while (j < b.length) parts.push({ kind: 'added', text: b[j++] })
  return merge(parts)
}

function merge(parts: DiffPart[]): DiffPart[] {
  const out: DiffPart[] = []
  for (const p of parts) {
    const last = out[out.length - 1]
    if (last && last.kind === p.kind) last.text += p.text
    else out.push({ ...p })
  }
  return out
}

/** Share of the final text's words that were already in the AI draft (1 = accepted as-is). */
export function retainedRatio(before: string, after: string): number {
  const parts = diffWords(before, after)
  const words = (kinds: DiffPart['kind'][]) =>
    parts.filter((p) => kinds.includes(p.kind)).reduce((n, p) => n + tokenize(p.text).filter((t) => t.trim()).length, 0)
  const total = words(['same', 'added'])
  return total === 0 ? 1 : words(['same']) / total
}
