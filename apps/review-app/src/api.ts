// Typed client for the encounter service REST API.

export type Role = 'CLINICIAN' | 'SCRIBE' | 'ADMIN' | 'SERVICE'
export type EncounterStatus = 'DRAFTING' | 'IN_REVIEW' | 'APPROVED' | 'FILED' | 'DRAFT_FAILED'
export type AuthorType = 'AI' | 'HUMAN'

export interface Session {
  token: string
  expiresAt: string
  username: string
  role: Role
  displayName: string
}

export interface SoapNote {
  subjective: string
  objective: string
  assessment: string
  plan: string
}

export const SOAP_SECTIONS: (keyof SoapNote)[] = ['subjective', 'objective', 'assessment', 'plan']

export interface SafetyFlag {
  code: string
  severity: 'high' | 'medium' | 'low'
  subject: string
  message: string
  section: string
}

export interface EncounterSummary {
  id: string
  patientId: string
  patientName: string
  status: EncounterStatus
  createdBy: string
  createdAt: string
  draftedAt: string | null
  currentVersion: number
  safetyFlagCount: number
}

export interface NoteVersionMeta {
  version: number
  authorType: AuthorType
  author: string
  createdAt: string
  model: string | null
  safetyFlagCount: number
}

export interface NoteVersionView extends Omit<NoteVersionMeta, 'safetyFlagCount'> {
  soap: SoapNote
  safetyFlags: SafetyFlag[]
}

export interface EncounterDetail {
  summary: EncounterSummary
  transcript: string
  versions: NoteVersionMeta[]
  latest: NoteVersionView | null
  draftSafetyFlags: SafetyFlag[]
  approvedVersion: number | null
  approvedBy: string | null
  approvedAt: string | null
  fhirDocumentId: string | null
  failureReason: string | null
}

export interface PatientSummary {
  id: string
  name: string
  gender: string
  birthDate: string | null
}

export interface PatientContext {
  ageYears: number | null
  gender: string
  conditions: string[]
  medications: string[]
  allergies: string[]
}

export interface AdminEncounterView {
  id: string
  status: EncounterStatus
  createdAt: string
  draftedAt: string | null
  updatedAt: string
  draftAttempt: number
  failureReason: string | null
  fhirDocumentId: string | null
}

export interface AuditEntry {
  id: number
  occurredAt: string
  actor: string
  actorRole: string
  action: string
  encounterId: string | null
  noteVersion: number | null
  detail: string
}

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

const SESSION_KEY = 'chartwise.session'

// sessionStorage, not localStorage: the token dies with the tab, which suits a PHI application.
export function loadSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY)
    if (!raw) return null
    const session = JSON.parse(raw) as Session
    return new Date(session.expiresAt).getTime() > Date.now() ? session : null
  } catch {
    return null
  }
}

export function saveSession(session: Session | null): void {
  try {
    if (session) sessionStorage.setItem(SESSION_KEY, JSON.stringify(session))
    else sessionStorage.removeItem(SESSION_KEY)
  } catch {
    // storage unavailable (private mode); the session lives in memory only
  }
}

let onUnauthorized: () => void = () => {}
export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const session = loadSession()
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (session) headers.Authorization = `Bearer ${session.token}`

  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  if (res.status === 401 && path !== '/api/auth/login') {
    onUnauthorized()
  }
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`
    try {
      const problem = await res.json()
      message = problem.detail ?? problem.title ?? message
    } catch {
      // not JSON
    }
    throw new ApiError(res.status, message)
  }
  return (res.status === 204 ? undefined : await res.json()) as T
}

export const api = {
  login: (username: string, password: string) => request<Session>('POST', '/api/auth/login', { username, password }),
  patients: (name?: string) =>
    request<PatientSummary[]>('GET', `/api/patients${name ? `?name=${encodeURIComponent(name)}` : ''}`),
  encounters: (status?: EncounterStatus) =>
    request<EncounterSummary[]>('GET', `/api/encounters${status ? `?status=${status}` : ''}`),
  createEncounter: (patientId: string, transcript: string) =>
    request<EncounterSummary>('POST', '/api/encounters', { patientId, transcript }),
  encounter: (id: string) => request<EncounterDetail>('GET', `/api/encounters/${id}`),
  version: (id: string, version: number) => request<NoteVersionView>('GET', `/api/encounters/${id}/notes/${version}`),
  patientContext: (id: string) => request<PatientContext>('GET', `/api/encounters/${id}/patient-context`),
  saveNote: (id: string, baseVersion: number, soap: SoapNote) =>
    request<NoteVersionView>('POST', `/api/encounters/${id}/notes`, { baseVersion, soap }),
  approve: (id: string, version: number) => request<EncounterSummary>('POST', `/api/encounters/${id}/approve`, { version }),
  adminEncounters: (status?: EncounterStatus) =>
    request<AdminEncounterView[]>('GET', `/api/admin/encounters${status ? `?status=${status}` : ''}`),
  retry: (id: string) => request<AdminEncounterView>('POST', `/api/admin/encounters/${id}/retry`),
  audit: (encounterId?: string) =>
    request<AuditEntry[]>('GET', `/api/audit${encounterId ? `?encounterId=${encounterId}` : ''}`),
}
