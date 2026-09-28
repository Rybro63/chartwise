package com.chartwise.encounter.encounter;

import com.chartwise.encounter.crypto.EncryptedStringConverter;
import jakarta.persistence.Column;
import jakarta.persistence.Convert;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "encounter")
public class Encounter {

    @Id
    private UUID id;

    @Column(name = "patient_id", nullable = false)
    private String patientId;

    @Convert(converter = EncryptedStringConverter.class)
    @Column(name = "patient_name_ciphertext", nullable = false)
    private String patientName;

    @Convert(converter = EncryptedStringConverter.class)
    @Column(name = "transcript_ciphertext", nullable = false)
    private String transcript;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    private EncounterStatus status;

    @Column(name = "created_by", nullable = false)
    private String createdBy;

    @Column(name = "current_version", nullable = false)
    private int currentVersion;

    @Column(name = "draft_attempt", nullable = false)
    private int draftAttempt;

    @Column(name = "safety_flag_count", nullable = false)
    private int safetyFlagCount;

    @Column(name = "approved_version")
    private Integer approvedVersion;

    @Column(name = "approved_by")
    private String approvedBy;

    @Column(name = "approved_at")
    private Instant approvedAt;

    @Column(name = "fhir_document_id")
    private String fhirDocumentId;

    @Column(name = "failure_reason")
    private String failureReason;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "drafted_at")
    private Instant draftedAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @Version
    @Column(name = "lock_version", nullable = false)
    private long lockVersion;

    protected Encounter() {}

    public Encounter(String patientId, String patientName, String transcript, String createdBy) {
        Instant now = Instant.now();
        this.id = UUID.randomUUID();
        this.patientId = patientId;
        this.patientName = patientName;
        this.transcript = transcript;
        this.createdBy = createdBy;
        this.status = EncounterStatus.DRAFTING;
        this.draftAttempt = 1;
        this.createdAt = now;
        this.updatedAt = now;
    }

    public void acceptDraft(int flagCount) {
        requireStatus(EncounterStatus.DRAFTING);
        this.status = EncounterStatus.IN_REVIEW;
        this.currentVersion = 1;
        this.safetyFlagCount = flagCount;
        this.draftedAt = Instant.now();
        this.failureReason = null;
        touch();
    }

    public int nextVersion() {
        requireStatus(EncounterStatus.IN_REVIEW);
        this.currentVersion += 1;
        touch();
        return currentVersion;
    }

    public void approve(int version, String clinician) {
        requireStatus(EncounterStatus.IN_REVIEW);
        this.status = EncounterStatus.APPROVED;
        this.approvedVersion = version;
        this.approvedBy = clinician;
        this.approvedAt = Instant.now();
        touch();
    }

    public void markFiled(String documentId) {
        this.status = EncounterStatus.FILED;
        this.fhirDocumentId = documentId;
        this.failureReason = null;
        touch();
    }

    public void markDraftFailed(String reason) {
        requireStatus(EncounterStatus.DRAFTING);
        this.status = EncounterStatus.DRAFT_FAILED;
        this.failureReason = reason;
        touch();
    }

    public void markFilingFailed(String reason) {
        this.failureReason = reason;
        touch();
    }

    public void retryDraft() {
        requireStatus(EncounterStatus.DRAFT_FAILED);
        this.status = EncounterStatus.DRAFTING;
        this.draftAttempt += 1;
        this.failureReason = null;
        touch();
    }

    private void requireStatus(EncounterStatus expected) {
        if (status != expected) {
            throw new InvalidStateException("encounter is " + status + ", expected " + expected);
        }
    }

    private void touch() {
        this.updatedAt = Instant.now();
    }

    public UUID getId() { return id; }
    public String getPatientId() { return patientId; }
    public String getPatientName() { return patientName; }
    public String getTranscript() { return transcript; }
    public EncounterStatus getStatus() { return status; }
    public String getCreatedBy() { return createdBy; }
    public int getCurrentVersion() { return currentVersion; }
    public int getDraftAttempt() { return draftAttempt; }
    public int getSafetyFlagCount() { return safetyFlagCount; }
    public Integer getApprovedVersion() { return approvedVersion; }
    public String getApprovedBy() { return approvedBy; }
    public Instant getApprovedAt() { return approvedAt; }
    public String getFhirDocumentId() { return fhirDocumentId; }
    public String getFailureReason() { return failureReason; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getDraftedAt() { return draftedAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
