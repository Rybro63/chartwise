package com.chartwise.encounter.encounter;

/**
 * A problem the note worker's safety check found in an AI draft, e.g. code
 * {@code UNSUPPORTED_MEDICATION} for a drug that is in neither the patient's record nor the
 * transcript.
 */
public record SafetyFlag(String code, String severity, String subject, String message, String section) {}
