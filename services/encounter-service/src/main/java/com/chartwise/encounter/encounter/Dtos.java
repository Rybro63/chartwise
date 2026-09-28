package com.chartwise.encounter.encounter;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

public final class Dtos {

    private Dtos() {}

    public record CreateEncounterRequest(
            @NotBlank @Size(max = 64) String patientId,
            @NotBlank @Size(max = 200_000) String transcript) {}

    public record SaveNoteRequest(@NotNull @Positive Integer baseVersion, @NotNull @Valid SoapNote soap) {}

    public record ApproveRequest(@NotNull @Positive Integer version) {}

    public record EncounterSummary(
            UUID id,
            String patientId,
            String patientName,
            EncounterStatus status,
            String createdBy,
            Instant createdAt,
            Instant draftedAt,
            int currentVersion,
            int safetyFlagCount) {

        static EncounterSummary of(Encounter e) {
            return new EncounterSummary(e.getId(), e.getPatientId(), e.getPatientName(), e.getStatus(), e.getCreatedBy(),
                    e.getCreatedAt(), e.getDraftedAt(), e.getCurrentVersion(), e.getSafetyFlagCount());
        }
    }

    public record NoteVersionMeta(int version, AuthorType authorType, String author, Instant createdAt, String model,
                                  int safetyFlagCount) {}

    public record NoteVersionView(int version, AuthorType authorType, String author, Instant createdAt, String model,
                                  SoapNote soap, List<SafetyFlag> safetyFlags) {}

    public record EncounterDetail(
            EncounterSummary summary,
            String transcript,
            List<NoteVersionMeta> versions,
            NoteVersionView latest,
            List<SafetyFlag> draftSafetyFlags,
            Integer approvedVersion,
            String approvedBy,
            Instant approvedAt,
            String fhirDocumentId,
            String failureReason) {}

    /** Operational view for admins: no names, transcripts or note content. */
    public record AdminEncounterView(
            UUID id,
            EncounterStatus status,
            Instant createdAt,
            Instant draftedAt,
            Instant updatedAt,
            int draftAttempt,
            String failureReason,
            String fhirDocumentId) {

        static AdminEncounterView of(Encounter e) {
            return new AdminEncounterView(e.getId(), e.getStatus(), e.getCreatedAt(), e.getDraftedAt(), e.getUpdatedAt(),
                    e.getDraftAttempt(), e.getFailureReason(), e.getFhirDocumentId());
        }
    }

    /** What the note worker needs to draft a note. Served only to the SERVICE role. */
    public record DraftContext(
            UUID encounterId,
            EncounterStatus status,
            int attempt,
            String transcript,
            Integer patientAgeYears,
            String patientGender,
            List<String> conditions,
            List<String> medications,
            List<String> allergies) {}
}
