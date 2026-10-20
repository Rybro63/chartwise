package com.chartwise.encounter.encounter;

/**
 * DRAFTING -> IN_REVIEW -> APPROVED -> FILED, with DRAFTING -> DRAFT_FAILED when a draft lands on
 * the dead-letter topic. An admin retry moves DRAFT_FAILED back to DRAFTING. A visit uploaded as a
 * recording starts in TRANSCRIBING and moves to DRAFTING when its transcript arrives (or to
 * DRAFT_FAILED if transcription fails; a retry then transcribes again).
 */
public enum EncounterStatus {
    TRANSCRIBING,
    DRAFTING,
    IN_REVIEW,
    APPROVED,
    FILED,
    DRAFT_FAILED
}
