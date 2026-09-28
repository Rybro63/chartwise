package com.chartwise.encounter.encounter;

/**
 * DRAFTING -> IN_REVIEW -> APPROVED -> FILED, with DRAFTING -> DRAFT_FAILED when a draft lands on
 * the dead-letter topic. An admin retry moves DRAFT_FAILED back to DRAFTING.
 */
public enum EncounterStatus {
    DRAFTING,
    IN_REVIEW,
    APPROVED,
    FILED,
    DRAFT_FAILED
}
