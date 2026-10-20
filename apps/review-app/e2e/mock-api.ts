import type { Page, Route } from '@playwright/test'
import type { DraftDetail, EncounterDetail, NoteVersionView, Role, SoapNote } from '../src/api'

// Synthetic visit, in the shape the encounter service returns.
export const ENCOUNTER_ID = '6f1c2a9e-0000-4000-8000-000000000001'
export const TRANSCRIPT = [
  'Doctor: How has the blood pressure been?',
  'Patient: I get headaches in the morning.',
  "Doctor: You're on lisinopril, ten milligrams.",
  'Patient: Every day.',
  "Doctor: Let's go up to twenty milligrams.",
  'Doctor: See you back in four weeks.',
].join('\n')

const AI_DRAFT: SoapNote = {
  subjective: 'Reports morning headaches.',
  objective: 'Exam not documented.',
  assessment: 'Essential hypertension.',
  plan: 'Increase lisinopril to 20 mg daily.\nStart aspirin 81 mg daily.\nFollow up in 4 weeks.',
}

const DETAIL: DraftDetail = {
  evidence: [
    { section: 'subjective', text: 'Reports morning headaches.', lines: [2], fromRecord: false, absence: false },
    { section: 'objective', text: 'Exam not documented.', lines: [], fromRecord: false, absence: true },
    { section: 'assessment', text: 'Essential hypertension.', lines: [], fromRecord: true, absence: false },
    { section: 'plan', text: 'Increase lisinopril to 20 mg daily.', lines: [3, 5], fromRecord: false, absence: false },
    { section: 'plan', text: 'Start aspirin 81 mg daily.', lines: [], fromRecord: false, absence: false },
    { section: 'plan', text: 'Follow up in 4 weeks.', lines: [6], fromRecord: false, absence: false },
  ],
  medications: [
    { name: 'lisinopril', status: 'changed', dose: '20 mg' },
    { name: 'aspirin', status: 'started', dose: '81 mg' },
  ],
}

export interface MockState {
  versions: NoteVersionView[]
  status: 'IN_REVIEW' | 'APPROVED' | 'FILED'
  approvedVersion: number | null
  /** When set, the next save is rejected as if someone else had saved first. */
  conflictOnNextSave: boolean
}

export async function mockApi(page: Page, role: Role): Promise<MockState> {
  const state: MockState = {
    versions: [{ version: 1, authorType: 'AI', author: 'note-worker', createdAt: '2026-10-19T14:00:05Z',
                 model: 'claude-opus-5-5', soap: AI_DRAFT, safetyFlags: [] }],
    status: 'IN_REVIEW',
    approvedVersion: null,
    conflictOnNextSave: false,
  }
  const summary = () => ({
    id: ENCOUNTER_ID, patientId: 'p1', patientName: 'Synthetic Patient', status: state.status, createdBy: 'scribe',
    createdAt: '2026-10-19T14:00:00Z', draftedAt: '2026-10-19T14:00:05Z',
    currentVersion: state.versions.length, safetyFlagCount: 1,
  })
  const detail = (): EncounterDetail => ({
    summary: summary(),
    transcript: TRANSCRIPT,
    versions: state.versions.map(({ soap: _soap, safetyFlags, ...meta }) => ({ ...meta, safetyFlagCount: safetyFlags.length })),
    latest: state.versions.at(-1)!,
    draftSafetyFlags: [{ code: 'UNSUPPORTED_STATEMENT', severity: 'medium', subject: 'Start aspirin 81 mg daily.',
                         message: 'This statement cites no line of the transcript and no part of the record.', section: 'plan' }],
    draftDetail: DETAIL,
    approvedVersion: state.approvedVersion,
    approvedBy: state.approvedVersion ? 'clinician' : null,
    approvedAt: state.approvedVersion ? '2026-10-19T14:10:00Z' : null,
    fhirDocumentId: null,
    failureReason: null,
  })
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    const method = route.request().method()
    const path = url.pathname

    if (path === '/api/auth/login') {
      return json(route, { token: 'test-token', expiresAt: '2099-01-01T00:00:00Z', username: role.toLowerCase(), role,
                           displayName: role === 'CLINICIAN' ? 'Dr. Test' : 'Test Scribe' })
    }
    if (path === '/api/encounters' && method === 'GET') return json(route, [summary()])
    if (path === `/api/encounters/${ENCOUNTER_ID}` && method === 'GET') return json(route, detail())
    if (path === `/api/encounters/${ENCOUNTER_ID}/patient-context`) {
      return json(route, { ageYears: 61, gender: 'female', conditions: ['Essential hypertension (disorder)'],
                           medications: ['lisinopril 10 MG Oral Tablet'], allergies: [] })
    }
    const version = path.match(new RegExp(`^/api/encounters/${ENCOUNTER_ID}/notes/(\\d+)$`))
    if (version) return json(route, state.versions[Number(version[1]) - 1])
    if (path === `/api/encounters/${ENCOUNTER_ID}/notes` && method === 'POST') {
      const body = route.request().postDataJSON() as { baseVersion: number; soap: SoapNote }
      if (state.conflictOnNextSave || body.baseVersion !== state.versions.length) {
        state.conflictOnNextSave = false
        return json(route, { title: 'Conflict', detail: `note was changed by someone else (latest is v${state.versions.length})` }, 409)
      }
      const saved: NoteVersionView = { version: state.versions.length + 1, authorType: 'HUMAN', author: role.toLowerCase(),
                                       createdAt: '2026-10-19T14:05:00Z', model: null, soap: body.soap, safetyFlags: [] }
      state.versions.push(saved)
      return json(route, saved, 201)
    }
    if (path === `/api/encounters/${ENCOUNTER_ID}/approve` && method === 'POST') {
      if (role !== 'CLINICIAN') return json(route, { title: 'Forbidden' }, 403)
      state.status = 'APPROVED'
      state.approvedVersion = (route.request().postDataJSON() as { version: number }).version
      return json(route, summary())
    }
    return json(route, { title: `unmocked ${method} ${path}` }, 500)
  })
  return state
}

export async function signIn(page: Page, username: string) {
  await page.goto('/')
  await page.getByLabel('Username').fill(username)
  await page.getByLabel('Password').fill('chartwise-dev')
  await page.getByRole('button', { name: 'Sign in' }).click()
}
