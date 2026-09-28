import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError, type EncounterDetail, type NoteVersionView, type PatientContext, type SoapNote } from '../api'
import { useAuth } from '../auth'
import { DiffView } from '../components/DiffView'
import { SafetyFlags } from '../components/SafetyFlags'
import { SoapEditor } from '../components/SoapEditor'
import { StatusBadge } from '../components/StatusBadge'
import { elapsed, formatTime } from '../lib/format'
import { usePolling } from '../lib/usePolling'

const sameNote = (a: SoapNote, b: SoapNote) =>
  a.subjective === b.subjective && a.objective === b.objective && a.assessment === b.assessment && a.plan === b.plan

export function EncounterPage() {
  const { id = '' } = useParams()
  const { session } = useAuth()
  const { data: detail, error, refresh } = usePolling<EncounterDetail>(
    () => api.encounter(id), 2000,
    (d) => !d || d.summary.status === 'DRAFTING' || d.summary.status === 'APPROVED')

  const [context, setContext] = useState<PatientContext>()
  // Edits are tied to the version they started from; when a new version loads they no longer apply.
  const [edit, setEdit] = useState<{ version: number; soap: SoapNote }>()
  const [aiDraft, setAiDraft] = useState<NoteVersionView>()
  const [compare, setCompare] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ kind: 'error' | 'ok'; text: string }>()

  const latest = detail?.latest ?? null
  const draft = latest ? (edit?.version === latest.version ? edit.soap : latest.soap) : undefined
  const setDraft = (soap: SoapNote) => latest && setEdit({ version: latest.version, soap })

  useEffect(() => {
    api.patientContext(id).then(setContext).catch(() => setContext(undefined))
  }, [id])

  useEffect(() => {
    if (detail && detail.versions.length > 1 && !aiDraft) api.version(id, 1).then(setAiDraft).catch(() => {})
  }, [detail, aiDraft, id])

  const dirty = useMemo(() => !!(draft && latest && !sameNote(draft, latest.soap)), [draft, latest])
  const editable = detail?.summary.status === 'IN_REVIEW'
  const canApprove = session?.role === 'CLINICIAN' && editable && !dirty

  async function save() {
    if (!detail || !latest || !draft) return
    setBusy(true)
    setMessage(undefined)
    try {
      const saved = await api.saveNote(id, latest.version, draft)
      await refresh()
      setMessage({ kind: 'ok', text: `Saved as version ${saved.version}.` })
    } catch (e) {
      setMessage({
        kind: 'error',
        text: e instanceof ApiError && e.status === 409 ? `${e.message}. Reload to see the latest version.` : String(e),
      })
    } finally {
      setBusy(false)
    }
  }

  async function approve() {
    if (!latest) return
    setBusy(true)
    setMessage(undefined)
    try {
      await api.approve(id, latest.version)
      await refresh()
      setMessage({ kind: 'ok', text: `Version ${latest.version} approved. Filing to the patient record…` })
    } catch (e) {
      setMessage({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  if (error && !detail) return <main className="page"><p className="error">{error}</p></main>
  if (!detail) return <main className="page"><p className="muted">Loading…</p></main>
  const s = detail.summary

  return (
    <main className="page wide">
      <Link to="/" className="back">← Queue</Link>
      <header className="encounter-head">
        <div>
          <h2>{s.patientName}</h2>
          <p className="muted">
            Recorded {formatTime(s.createdAt)} by {s.createdBy} · draft ready in {elapsed(s.createdAt, s.draftedAt)}
            {detail.approvedBy && ` · approved by ${detail.approvedBy} (v${detail.approvedVersion})`}
            {detail.fhirDocumentId && ` · FHIR DocumentReference/${detail.fhirDocumentId}`}
          </p>
        </div>
        <StatusBadge status={s.status} />
      </header>

      <div className="split">
        <section className="panel">
          <h3>Transcript</h3>
          <pre className="transcript">{detail.transcript}</pre>
          {context && (
            <div className="context">
              <h3>Patient record</h3>
              <p className="muted small">{context.ageYears ?? '?'} y · {context.gender}</p>
              <RecordList title="Active medications" items={context.medications} />
              <RecordList title="Conditions" items={context.conditions} />
              <RecordList title="Allergies" items={context.allergies} />
            </div>
          )}
        </section>

        <section className="panel">
          {s.status === 'DRAFTING' && <p className="drafting">Drafting the note… this page updates automatically.</p>}
          {s.status === 'DRAFT_FAILED' && (
            <p className="error">The draft could not be generated ({detail.failureReason}). An admin can retry it.</p>
          )}
          {detail.failureReason && s.status === 'APPROVED' && (
            <p className="error">Filing to the patient record failed ({detail.failureReason}). An admin can retry it.</p>
          )}

          {latest && draft && (
            <>
              <SafetyFlags flags={detail.draftSafetyFlags} />
              <div className="note-head">
                <h3>
                  Note v{latest.version}{' '}
                  <span className="muted small">
                    {latest.authorType === 'AI' ? `AI draft (${latest.model})` : `edited by ${latest.author}`}
                  </span>
                </h3>
                {detail.versions.length > 1 && (
                  <button className="link" onClick={() => setCompare(!compare)}>
                    {compare ? 'Hide changes' : 'Compare with AI draft'}
                  </button>
                )}
              </div>

              {compare && aiDraft ? (
                <DiffView before={aiDraft.soap} after={draft} beforeLabel="the AI draft (v1)"
                          afterLabel={dirty ? 'your unsaved edits' : `v${latest.version}`} />
              ) : (
                <SoapEditor value={draft} onChange={setDraft} readOnly={!editable} />
              )}

              {message && <p className={message.kind === 'error' ? 'error' : 'ok'}>{message.text}</p>}
              {editable && (
                <div className="actions">
                  <button onClick={() => setDraft(latest.soap)} disabled={!dirty || busy}>Discard changes</button>
                  <button onClick={save} disabled={!dirty || busy}>Save as v{latest.version + 1}</button>
                  {session?.role === 'CLINICIAN' ? (
                    <button className="primary" onClick={approve} disabled={!canApprove || busy}
                            title={dirty ? 'Save your changes before approving' : undefined}>
                      Approve v{latest.version}
                    </button>
                  ) : (
                    <span className="muted small">Only clinicians can approve.</span>
                  )}
                </div>
              )}

              <details className="history">
                <summary>Version history ({detail.versions.length})</summary>
                <ol>
                  {detail.versions.map((v) => (
                    <li key={v.version}>
                      v{v.version} · {v.authorType === 'AI' ? `AI (${v.model})` : v.author} · {formatTime(v.createdAt)}
                    </li>
                  ))}
                </ol>
              </details>
            </>
          )}
        </section>
      </div>
    </main>
  )
}

function RecordList({ title, items }: { title: string; items: string[] }) {
  return (
    <>
      <h4>{title}</h4>
      {items.length ? <ul>{items.map((i) => <li key={i}>{i}</li>)}</ul> : <p className="muted small">None recorded</p>}
    </>
  )
}
