import { useEffect, useState, type ChangeEvent, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type PatientSummary } from '../api'

type Source = 'recording' | 'transcript'

export function NewEncounterPage() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [patients, setPatients] = useState<PatientSummary[]>([])
  const [patientId, setPatientId] = useState('')
  const [source, setSource] = useState<Source>('recording')
  const [transcript, setTranscript] = useState('')
  const [recording, setRecording] = useState<File>()
  const [error, setError] = useState<string>()
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const t = setTimeout(() => {
      api.patients(query || undefined).then(setPatients).catch((e: Error) => setError(e.message))
    }, 250)
    return () => clearTimeout(t)
  }, [query])

  async function onTranscriptFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (file) setTranscript(await file.text())
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(undefined)
    try {
      const created = source === 'recording' && recording
        ? await api.uploadRecording(patientId, recording)
        : await api.createEncounter(patientId, transcript)
      navigate(`/encounters/${created.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  const ready = !!patientId && (source === 'recording' ? !!recording : !!transcript.trim())

  return (
    <main className="page narrow">
      <h2>New visit</h2>
      <p className="muted">
        Upload the visit recording or its transcript. Chartwise transcribes the recording, drafts a SOAP note and
        adds it to the review queue.
      </p>
      <form onSubmit={submit} className="card form">
        <label>
          Find patient
          <input placeholder="Search by name" value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <label>
          Patient
          <select value={patientId} onChange={(e) => setPatientId(e.target.value)} required>
            <option value="">Select a patient…</option>
            {patients.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} · {p.gender} · born {p.birthDate ?? 'unknown'}
              </option>
            ))}
          </select>
        </label>
        <div className="tabs" role="tablist">
          <button type="button" role="tab" aria-selected={source === 'recording'}
                  className={source === 'recording' ? 'tab active' : 'tab'} onClick={() => setSource('recording')}>
            Recording
          </button>
          <button type="button" role="tab" aria-selected={source === 'transcript'}
                  className={source === 'transcript' ? 'tab active' : 'tab'} onClick={() => setSource('transcript')}>
            Transcript
          </button>
        </div>
        {source === 'recording' ? (
          <label className="file">
            Visit recording
            <input type="file" accept="audio/*" onChange={(e) => setRecording(e.target.files?.[0])} />
            <span className="muted small">
              Two-channel recordings (clinician on the left, patient on the right) are transcribed with speaker labels;
              one-channel recordings are transcribed without them. Transcription runs inside Chartwise.
            </span>
          </label>
        ) : (
          <>
            <label>
              Transcript
              <textarea rows={14} value={transcript} onChange={(e) => setTranscript(e.target.value)}
                        placeholder={'Doctor: What brings you in today?\nPatient: …'} />
            </label>
            <label className="file">
              Or load a .txt file <input type="file" accept=".txt,text/plain" onChange={onTranscriptFile} />
            </label>
          </>
        )}
        {error && <p className="error">{error}</p>}
        <button type="submit" className="primary" disabled={busy || !ready}>
          {busy ? 'Submitting…' : 'Create encounter'}
        </button>
      </form>
    </main>
  )
}
