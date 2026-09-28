// k6 load test: scribes uploading visit transcripts at a rising rate.
//
// k6 measures the API side (create latency, errors). The end-to-end SLO, encounter created to
// draft ready, is asynchronous, so it is measured server-side by the encounter service's
// chartwise_note_draft_latency_seconds histogram and read from Prometheus afterwards
// (loadtest/report.py).
//
// Run inside the compose network:
//   docker run --rm --network chartwise_default -v "$PWD":/work -w /work grafana/k6 \
//     run -e API=http://encounter-service:8080 loadtest/k6/drafting.js
import http from 'k6/http'
import { check } from 'k6'
import { SharedArray } from 'k6/data'

const API = __ENV.API || 'http://localhost:8080'
const PEAK = Number(__ENV.PEAK_RATE || 8)        // encounters per second at the top of the ramp
const STAGE = __ENV.STAGE || '1m'

const visits = new SharedArray('visits', () => JSON.parse(open('../../tools/transcripts/out/visits.json')))

export const options = {
  scenarios: {
    uploads: {
      executor: 'ramping-arrival-rate',
      startRate: 1,
      timeUnit: '1s',
      preAllocatedVUs: 20,
      maxVUs: 100,
      stages: [
        { target: Math.max(1, Math.round(PEAK / 4)), duration: STAGE },
        { target: Math.max(1, Math.round(PEAK / 2)), duration: STAGE },
        { target: Math.round((PEAK * 3) / 4), duration: STAGE },
        { target: PEAK, duration: STAGE },
        { target: 0, duration: '10s' },
      ],
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    'http_req_duration{name:create}': ['p(95)<500'],
  },
}

export function setup() {
  const res = http.post(`${API}/api/auth/login`, JSON.stringify({ username: 'scribe', password: 'chartwise-dev' }), {
    headers: { 'Content-Type': 'application/json' },
  })
  check(res, { 'logged in': (r) => r.status === 200 })
  return { token: res.json('token') }
}

export default function (data) {
  const visit = visits[Math.floor(Math.random() * visits.length)]
  const res = http.post(`${API}/api/encounters`, JSON.stringify(visit), {
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${data.token}` },
    tags: { name: 'create' },
  })
  check(res, { 'accepted (202)': (r) => r.status === 202 })
}
