import type { EncounterStatus } from '../api'

const LABELS: Record<EncounterStatus, string> = {
  DRAFTING: 'Drafting',
  IN_REVIEW: 'Needs review',
  APPROVED: 'Approved — filing',
  FILED: 'Filed',
  DRAFT_FAILED: 'Draft failed',
}

export function StatusBadge({ status }: { status: EncounterStatus }) {
  return <span className={`badge badge-${status.toLowerCase()}`}>{LABELS[status]}</span>
}
