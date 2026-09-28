package com.chartwise.encounter.events;

import com.chartwise.encounter.encounter.SafetyFlag;
import com.chartwise.encounter.encounter.SoapNote;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

/**
 * Kafka payloads (JSON). Every event is keyed by encounter ID. Events published by this service
 * (through the outbox) carry identifiers only; consumers fetch PHI through authenticated APIs.
 */
public final class Events {

    private Events() {}

    public record EncounterCreated(
            UUID eventId, String eventType, UUID encounterId, String patientId, int attempt, Instant occurredAt) {

        public static EncounterCreated of(UUID encounterId, String patientId, int attempt) {
            return new EncounterCreated(UUID.randomUUID(), "EncounterCreated", encounterId, patientId, attempt, Instant.now());
        }
    }

    /** Published by the note worker. */
    public record NoteDrafted(
            UUID eventId,
            String eventType,
            UUID encounterId,
            Integer attempt,
            SoapNote soap,
            List<SafetyFlag> safetyFlags,
            String model,
            String promptVersion,
            Integer gatewayAttempts,
            Instant occurredAt) {}

    public record NoteApproved(
            UUID eventId, String eventType, UUID encounterId, String patientId, int version, String approvedBy, Instant occurredAt) {

        public static NoteApproved of(UUID encounterId, String patientId, int version, String approvedBy) {
            return new NoteApproved(UUID.randomUUID(), "NoteApproved", encounterId, patientId, version, approvedBy, Instant.now());
        }
    }
}
