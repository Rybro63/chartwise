package com.chartwise.encounter.events;

import com.chartwise.encounter.encounter.EncounterService;
import com.chartwise.encounter.encounter.EncounterStatus;
import com.chartwise.encounter.encounter.EncounterRepository;
import com.chartwise.encounter.encounter.NoteVersionRepository;
import com.chartwise.encounter.encounter.SoapNote;
import com.chartwise.encounter.fhir.FhirGateway;
import com.chartwise.encounter.fhir.FiledNote;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.common.header.Header;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.KafkaHeaders;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;

@Component
public class EventListeners {

    private static final Logger log = LoggerFactory.getLogger(EventListeners.class);

    private final EncounterService service;
    private final EncounterRepository encounters;
    private final NoteVersionRepository versions;
    private final FhirGateway fhir;
    private final JsonMapper json;

    public EventListeners(EncounterService service, EncounterRepository encounters, NoteVersionRepository versions,
                          FhirGateway fhir, JsonMapper json) {
        this.service = service;
        this.encounters = encounters;
        this.versions = versions;
        this.fhir = fhir;
        this.json = json;
    }

    @KafkaListener(id = "note-drafted", topics = Topics.NOTE_DRAFTED)
    public void onNoteDrafted(ConsumerRecord<String, String> record) {
        service.applyDraft(json.readValue(record.value(), Events.NoteDrafted.class));
    }

    /**
     * Files the approved note to FHIR. The FHIR write can't join the database transaction, so
     * safety comes from ordering: write to FHIR first (conditional create, idempotent by encounter
     * identifier), then record FILED. A crash in between just repeats the conditional create.
     */
    @KafkaListener(id = "note-approved", topics = Topics.NOTE_APPROVED)
    public void onNoteApproved(ConsumerRecord<String, String> record) {
        Events.NoteApproved event = json.readValue(record.value(), Events.NoteApproved.class);
        var encounter = encounters.findById(event.encounterId()).orElse(null);
        if (encounter == null || encounter.getStatus() != EncounterStatus.APPROVED) {
            return; // already filed, or purged
        }
        var version = versions.findByEncounterIdAndVersion(encounter.getId(), encounter.getApprovedVersion()).orElseThrow();
        SoapNote soap = json.readValue(version.getContentJson(), SoapNote.class);
        String documentId = fhir.fileNote(new FiledNote(encounter.getId(), encounter.getPatientId(),
                encounter.getApprovedBy(), encounter.getApprovedAt(), soap.render()));
        service.markFiled(encounter.getId(), documentId);
    }

    /**
     * Turns dead letters into visible state (DRAFT_FAILED, or a filing failure reason) so they
     * show up in the admin view instead of disappearing. Never throws: a dead-letter handler that
     * dead-letters would loop.
     */
    @KafkaListener(id = "dead-letter", topics = Topics.DEAD_LETTER, groupId = "encounter-service-dead-letter")
    public void onDeadLetter(ConsumerRecord<String, String> record) {
        try {
            String originalTopic = header(record, KafkaHeaders.DLT_ORIGINAL_TOPIC);
            String exception = header(record, KafkaHeaders.DLT_EXCEPTION_CAUSE_FQCN);
            if (exception == null) {
                exception = header(record, KafkaHeaders.DLT_EXCEPTION_FQCN);
            }
            String reason = shortName(exception) + trimmed(header(record, KafkaHeaders.DLT_EXCEPTION_MESSAGE));
            service.handleDeadLetter(UUID.fromString(record.key()), originalTopic, reason);
        } catch (RuntimeException e) {
            log.error("Could not process dead letter at {}-{}@{}: {}", record.topic(), record.partition(), record.offset(),
                    e.getClass().getSimpleName());
        }
    }

    private static String header(ConsumerRecord<?, ?> record, String name) {
        Header header = record.headers().lastHeader(name);
        return header == null ? null : new String(header.value(), StandardCharsets.UTF_8);
    }

    private static String shortName(String fqcn) {
        if (fqcn == null) {
            return "UnknownError";
        }
        return fqcn.substring(fqcn.lastIndexOf('.') + 1);
    }

    /**
     * Exception messages come from our own services, which keep PHI out of them. Spring's wrapper
     * message is dropped because it only restates the listener method.
     */
    private static String trimmed(String message) {
        if (message == null || message.isBlank() || message.startsWith("Listener method")) {
            return "";
        }
        String oneLine = message.replaceAll("\\s+", " ").strip();
        return ": " + (oneLine.length() > 200 ? oneLine.substring(0, 200) : oneLine);
    }
}
