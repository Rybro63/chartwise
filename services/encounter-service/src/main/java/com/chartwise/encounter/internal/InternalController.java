package com.chartwise.encounter.internal;

import com.chartwise.encounter.encounter.Dtos.DraftContext;
import com.chartwise.encounter.encounter.Dtos.RecordingInfo;
import com.chartwise.encounter.encounter.Dtos.TranscriptSubmission;
import com.chartwise.encounter.encounter.EncounterService;
import com.chartwise.encounter.security.CurrentUser;
import jakarta.validation.Valid;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/**
 * Service-to-service endpoints (SERVICE role only). Events carry IDs; the worker fetches the PHI it
 * needs here, over an authenticated, audited call, instead of it being copied into Kafka.
 */
@RestController
public class InternalController {

    private final EncounterService service;

    public InternalController(EncounterService service) {
        this.service = service;
    }

    @GetMapping("/internal/encounters/{id}/draft-context")
    public DraftContext draftContext(@PathVariable UUID id) {
        return service.draftContext(id);
    }

    @GetMapping("/internal/encounters/{id}/recording")
    public RecordingInfo recording(@PathVariable UUID id) {
        return service.recordingInfo(id, CurrentUser.get());
    }

    @GetMapping("/internal/encounters/{id}/recording/audio")
    public ResponseEntity<byte[]> recordingAudio(@PathVariable UUID id) {
        var recording = service.recordingAudio(id, CurrentUser.get());
        return ResponseEntity.ok().contentType(MediaType.parseMediaType(recording.contentType())).body(recording.audio());
    }

    @PostMapping("/internal/encounters/{id}/transcript")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void transcript(@PathVariable UUID id, @Valid @RequestBody TranscriptSubmission submission) {
        service.applyTranscript(id, submission, CurrentUser.get());
    }
}
