export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function elapsed(fromIso: string, toIso: string | null): string {
  if (!toIso) return '—'
  const s = (new Date(toIso).getTime() - new Date(fromIso).getTime()) / 1000
  return s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`
}
