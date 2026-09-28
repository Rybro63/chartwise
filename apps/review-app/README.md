# Chartwise review app

The clinician-facing UI: review queue, transcript and AI draft side by side, versioned edits,
diff against the AI draft, approval, and an operations view for admins.

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api to http://localhost:8080 (override with API_URL)
npm test           # vitest
npm run lint       # oxlint
npm run build      # typecheck + production build
```
