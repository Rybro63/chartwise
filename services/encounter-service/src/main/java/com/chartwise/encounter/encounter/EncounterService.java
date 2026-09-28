package com.chartwise.encounter.encounter;

import com.chartwise.encounter.audit.AuditAction;
import com.chartwise.encounter.audit.AuditLog;
import com.chartwise.encounter.encounter.Dtos.AdminEncounterView;
import com.chartwise.encounter.encounter.Dtos.DraftContext;
import com.chartwise.encounter.encounter.Dtos.EncounterDetail;
import com.chartwise.encounter.encounter.Dtos.EncounterSummary;
import com.chartwise.encounter.encounter.Dtos.NoteVersionMeta;
import com.chartwise.encounter.encounter.Dtos.NoteVersionView;
import com.chartwise.encounter.events.Events;
import com.chartwise.encounter.events.IdempotencyGuard;
import com.chartwise.encounter.events.OutboxWriter;
import com.chartwise.encounter.events.Topics;
import com.chartwise.encounter.fhir.FhirGateway;
import com.chartwise.encounter.fhir.PatientContext;
import com.chartwise.encounter.fhir.PatientSummary;
import com.chartwise.encounter.security.CurrentUser;
import com.chartwise.encounter.security.Role;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.json.JsonMapper;

@Service
public class EncounterService {

    private static final Logger log = LoggerFactory.getLogger(EncounterService.class);
    private static final TypeReference<List<SafetyFlag>> FLAG_LIST = new TypeReference<>() {};
    private static final CurrentUser NOTE_WORKER = new CurrentUser("note-worker", Role.SERVICE);

    private final EncounterRepository encounters;
    private final NoteVersionRepository versions;
    private final OutboxWriter outbox;
    private final IdempotencyGuard idempotency;
    private final AuditLog audit;
    private final FhirGateway fhir;
    private final JsonMapper json;
    private final ChartwiseMetrics metrics;
    private final TransactionTemplate tx;

    public EncounterService(EncounterRepository encounters, NoteVersionRepository versions, OutboxWriter outbox,
                            IdempotencyGuard idempotency, AuditLog audit, FhirGateway fhir, JsonMapper json,
                            ChartwiseMetrics metrics, TransactionTemplate tx) {
        this.encounters = encounters;
        this.versions = versions;
        this.outbox = outbox;
        this.idempotency = idempotency;
        this.audit = audit;
        this.fhir = fhir;
        this.json = json;
        this.metrics = metrics;
        this.tx = tx;
    }

    // ---- Clinician / scribe API -------------------------------------------------------------

    public EncounterSummary create(String patientId, String transcript, CurrentUser user) {
        // Validate the patient outside the transaction: no DB connection held during a network call.
        PatientSummary patient = fhir.findPatient(patientId)
                .orElseThrow(() -> new NotFoundException("patient not found"));

        Encounter encounter = tx.execute(status -> {
            Encounter e = encounters.save(new Encounter(patientId, patient.name(), transcript, user.username()));
            // Same transaction: the encounter can never exist without its event, or vice versa.
            outbox.append(e.getId(), Topics.ENCOUNTER_CREATED, Events.EncounterCreated.of(e.getId(), patientId, 1));
            audit.record(user, AuditAction.ENCOUNTER_CREATED, e.getId());
            return e;
        });
        metrics.encounterCreated();
        log.info("Encounter {} created by {}", encounter.getId(), user.username());
        return EncounterSummary.of(Objects.requireNonNull(encounter));
    }

    @Transactional(readOnly = true)
    public List<EncounterSummary> list(EncounterStatus status, int limit) {
        PageRequest page = PageRequest.of(0, Math.min(Math.max(limit, 1), 500));
        List<Encounter> rows = status == null
                ? encounters.findAllByOrderByCreatedAtDesc(page)
                : encounters.findByStatusOrderByCreatedAtAsc(status, page);
        return rows.stream().map(EncounterSummary::of).toList();
    }

    @Transactional
    public EncounterDetail detail(UUID id, CurrentUser user) {
        Encounter e = find(id);
        List<NoteVersion> all = versions.findByEncounterIdOrderByVersionAsc(id);
        List<NoteVersionMeta> metas = all.stream()
                .map(v -> new NoteVersionMeta(v.getVersion(), v.getAuthorType(), v.getAuthor(), v.getCreatedAt(),
                        v.getModel(), v.getSafetyFlagCount()))
                .toList();
        NoteVersionView latest = all.isEmpty() ? null : view(all.getLast());
        List<SafetyFlag> draftFlags = all.isEmpty() ? List.of() : flags(all.getFirst());
        audit.record(user, AuditAction.ENCOUNTER_VIEWED, id, latest == null ? null : latest.version(), Map.of());
        return new EncounterDetail(EncounterSummary.of(e), e.getTranscript(), metas, latest, draftFlags,
                e.getApprovedVersion(), e.getApprovedBy(), e.getApprovedAt(), e.getFhirDocumentId(), e.getFailureReason());
    }

    @Transactional
    public NoteVersionView version(UUID id, int version, CurrentUser user) {
        find(id);
        NoteVersion v = versions.findByEncounterIdAndVersion(id, version)
                .orElseThrow(() -> new NotFoundException("note version not found"));
        audit.record(user, AuditAction.NOTE_VERSION_VIEWED, id, version, Map.of());
        return view(v);
    }

    public PatientContext patientContext(UUID id, CurrentUser user) {
        Encounter e = tx.execute(status -> {
            Encounter found = find(id);
            audit.record(user, AuditAction.ENCOUNTER_VIEWED, id, null, Map.of("view", "patient-context"));
            return found;
        });
        return fhir.loadContext(Objects.requireNonNull(e).getPatientId());
    }

    /** Saves an edit as a new version. {@code baseVersion} must be the latest, or the edit is stale. */
    @Transactional
    public NoteVersionView saveEdit(UUID id, int baseVersion, SoapNote soap, CurrentUser user) {
        Encounter e = encounters.findForUpdate(id).orElseThrow(() -> new NotFoundException("encounter not found"));
        if (e.getStatus() != EncounterStatus.IN_REVIEW) {
            throw new InvalidStateException("encounter is " + e.getStatus() + "; only drafts in review can be edited");
        }
        if (baseVersion != e.getCurrentVersion()) {
            throw new StaleVersionException("note was changed by someone else (latest is v" + e.getCurrentVersion() + ")");
        }
        NoteVersion base = versions.findByEncounterIdAndVersion(id, baseVersion).orElseThrow();
        SoapNote previous = json.readValue(base.getContentJson(), SoapNote.class);
        if (previous.equals(soap)) {
            return view(base);
        }
        int next = e.nextVersion();
        NoteVersion saved = versions.save(new NoteVersion(id, next, AuthorType.HUMAN, user.username(),
                json.writeValueAsString(soap), "[]", 0, null));
        audit.record(user, AuditAction.NOTE_EDITED, id, next, Map.of("sectionsChanged", changedSections(previous, soap)));
        return view(saved);
    }

    /** Approves exactly the version the clinician reviewed; approving a stale version is rejected. */
    @Transactional
    public EncounterSummary approve(UUID id, int version, CurrentUser user) {
        Encounter e = encounters.findForUpdate(id).orElseThrow(() -> new NotFoundException("encounter not found"));
        if (e.getStatus() != EncounterStatus.IN_REVIEW) {
            throw new InvalidStateException("encounter is " + e.getStatus() + "; only drafts in review can be approved");
        }
        if (version != e.getCurrentVersion()) {
            throw new StaleVersionException("v" + version + " is not the latest version (v" + e.getCurrentVersion() + ")");
        }
        e.approve(version, user.username());
        outbox.append(id, Topics.NOTE_APPROVED, Events.NoteApproved.of(id, e.getPatientId(), version, user.username()));
        audit.record(user, AuditAction.NOTE_APPROVED, id, version,
                Map.of("aiDraftUnchanged", version == 1, "humanVersions", version - 1));
        metrics.noteApproved();
        log.info("Encounter {} approved at v{} by {}", id, version, user.username());
        return EncounterSummary.of(e);
    }

    // ---- Note worker (internal API) ----------------------------------------------------------

    public DraftContext draftContext(UUID id) {
        Encounter e = tx.execute(status -> {
            Encounter found = find(id);
            audit.record(NOTE_WORKER, AuditAction.DRAFT_CONTEXT_READ, id, null, Map.of("attempt", found.getDraftAttempt()));
            return found;
        });
        Objects.requireNonNull(e);
        if (e.getStatus() != EncounterStatus.DRAFTING) {
            // Already drafted (duplicate delivery) or failed: tell the worker without loading FHIR.
            return new DraftContext(id, e.getStatus(), e.getDraftAttempt(), null, null, null, List.of(), List.of(), List.of());
        }
        PatientContext ctx = fhir.loadContext(e.getPatientId());
        return new DraftContext(id, e.getStatus(), e.getDraftAttempt(), e.getTranscript(), ctx.ageYears(), ctx.gender(),
                ctx.conditions(), ctx.medications(), ctx.allergies());
    }

    // ---- Event handlers ----------------------------------------------------------------------

    /**
     * Handles note.drafted. Idempotent on encounter ID: the encounter row lock serialises
     * concurrent deliveries, and only the first draft for a DRAFTING encounter becomes version 1.
     * The processed_message record is written only when the draft is applied, so a draft that
     * arrives for a failed encounter can't block the draft from an admin retry.
     */
    @Transactional
    public void applyDraft(Events.NoteDrafted event) {
        UUID id = event.encounterId();
        Encounter e = encounters.findForUpdate(id).orElse(null);
        if (e == null) {
            log.warn("Draft for unknown encounter {} ignored (purged?)", id);
            return;
        }
        if (e.getStatus() != EncounterStatus.DRAFTING || !idempotency.firstDelivery("note-drafted", id.toString())) {
            idempotency.recordDuplicate("note-drafted");
            log.info("Duplicate draft for encounter {} ignored (status {})", id, e.getStatus());
            return;
        }
        List<SafetyFlag> flags = event.safetyFlags() == null ? List.of() : event.safetyFlags();
        versions.save(new NoteVersion(id, 1, AuthorType.AI, "note-worker", json.writeValueAsString(event.soap()),
                json.writeValueAsString(flags), flags.size(), event.model()));
        e.acceptDraft(flags.size());
        audit.record(NOTE_WORKER, AuditAction.DRAFT_RECEIVED, id, 1,
                Map.of("safetyFlags", flags.size(), "model", String.valueOf(event.model())));
        metrics.draftReady(Duration.between(e.getCreatedAt(), e.getDraftedAt()), flags.size());
        log.info("Encounter {} drafted with {} safety flag(s)", id, flags.size());
    }

    /** Records a successful FHIR write. Idempotent: a second call for a filed encounter is a no-op. */
    @Transactional
    public void markFiled(UUID id, String documentId) {
        Encounter e = encounters.findForUpdate(id).orElse(null);
        if (e == null || e.getStatus() != EncounterStatus.APPROVED || !idempotency.firstDelivery("note-filed", id.toString())) {
            idempotency.recordDuplicate("note-filed");
            return;
        }
        e.markFiled(documentId);
        audit.record(CurrentUser.SYSTEM, AuditAction.NOTE_FILED, id, e.getApprovedVersion(), Map.of("documentReference", documentId));
        metrics.noteFiled();
        log.info("Encounter {} filed to FHIR as DocumentReference/{}", id, documentId);
    }

    @Transactional
    public void handleDeadLetter(UUID id, String originalTopic, String reason) {
        metrics.deadLetter(originalTopic);
        Encounter e = encounters.findForUpdate(id).orElse(null);
        if (e == null) {
            return;
        }
        if ((Topics.ENCOUNTER_CREATED.equals(originalTopic) || Topics.NOTE_DRAFTED.equals(originalTopic))
                && e.getStatus() == EncounterStatus.DRAFTING) {
            e.markDraftFailed(reason);
            audit.record(CurrentUser.SYSTEM, AuditAction.DRAFT_FAILED, id, null, Map.of("reason", reason, "topic", originalTopic));
            log.warn("Encounter {} marked DRAFT_FAILED: {}", id, reason);
        } else if (Topics.NOTE_APPROVED.equals(originalTopic) && e.getStatus() == EncounterStatus.APPROVED) {
            e.markFilingFailed(reason);
            audit.record(CurrentUser.SYSTEM, AuditAction.FILING_FAILED, id, e.getApprovedVersion(), Map.of("reason", reason));
            log.warn("Encounter {} filing failed: {}", id, reason);
        }
    }

    // ---- Admin -------------------------------------------------------------------------------

    @Transactional(readOnly = true)
    public List<AdminEncounterView> adminList(EncounterStatus status, int limit) {
        PageRequest page = PageRequest.of(0, Math.min(Math.max(limit, 1), 500));
        List<Encounter> rows = status == null
                ? encounters.findAllByOrderByCreatedAtDesc(page)
                : encounters.findByStatusOrderByCreatedAtAsc(status, page);
        return rows.stream().map(AdminEncounterView::of).toList();
    }

    /** Re-drives a failed draft or a failed FHIR filing by emitting its event again. */
    @Transactional
    public AdminEncounterView retry(UUID id, CurrentUser admin) {
        Encounter e = encounters.findForUpdate(id).orElseThrow(() -> new NotFoundException("encounter not found"));
        switch (e.getStatus()) {
            case DRAFT_FAILED -> {
                e.retryDraft();
                outbox.append(id, Topics.ENCOUNTER_CREATED, Events.EncounterCreated.of(id, e.getPatientId(), e.getDraftAttempt()));
            }
            case APPROVED -> outbox.append(id, Topics.NOTE_APPROVED,
                    Events.NoteApproved.of(id, e.getPatientId(), e.getApprovedVersion(), e.getApprovedBy()));
            default -> throw new InvalidStateException("nothing to retry for an encounter in " + e.getStatus());
        }
        audit.record(admin, AuditAction.RETRY_REQUESTED, id, null, Map.of("status", e.getStatus().name()));
        return AdminEncounterView.of(e);
    }

    // ---- helpers -----------------------------------------------------------------------------

    private Encounter find(UUID id) {
        return encounters.findById(id).orElseThrow(() -> new NotFoundException("encounter not found"));
    }

    private NoteVersionView view(NoteVersion v) {
        return new NoteVersionView(v.getVersion(), v.getAuthorType(), v.getAuthor(), v.getCreatedAt(), v.getModel(),
                json.readValue(v.getContentJson(), SoapNote.class), flags(v));
    }

    private List<SafetyFlag> flags(NoteVersion v) {
        return json.readValue(v.getSafetyFlagsJson(), FLAG_LIST);
    }

    private static List<String> changedSections(SoapNote a, SoapNote b) {
        List<String> changed = new ArrayList<>();
        if (!a.subjective().equals(b.subjective())) changed.add("subjective");
        if (!a.objective().equals(b.objective())) changed.add("objective");
        if (!a.assessment().equals(b.assessment())) changed.add("assessment");
        if (!a.plan().equals(b.plan())) changed.add("plan");
        return changed;
    }
}
