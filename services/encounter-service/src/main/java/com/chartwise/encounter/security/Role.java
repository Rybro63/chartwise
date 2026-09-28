package com.chartwise.encounter.security;

public enum Role {
    /** Reviews, edits and approves notes. The only role that can sign off. */
    CLINICIAN,
    /** Uploads transcripts and edits drafts, but cannot approve. */
    SCRIBE,
    /** Operates the system: audit log, failed-encounter retries, retention. No PHI endpoints. */
    ADMIN,
    /** Machine account used by the note worker for internal endpoints. */
    SERVICE
}
