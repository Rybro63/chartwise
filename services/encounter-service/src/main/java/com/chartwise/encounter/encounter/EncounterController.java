package com.chartwise.encounter.encounter;

import com.chartwise.encounter.encounter.Dtos.ApproveRequest;
import com.chartwise.encounter.encounter.Dtos.CreateEncounterRequest;
import com.chartwise.encounter.encounter.Dtos.EncounterDetail;
import com.chartwise.encounter.encounter.Dtos.EncounterSummary;
import com.chartwise.encounter.encounter.Dtos.NoteVersionView;
import com.chartwise.encounter.encounter.Dtos.SaveNoteRequest;
import com.chartwise.encounter.fhir.PatientContext;
import com.chartwise.encounter.security.CurrentUser;
import jakarta.validation.Valid;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/encounters")
public class EncounterController {

    private final EncounterService service;

    public EncounterController(EncounterService service) {
        this.service = service;
    }

    /** Accepted, not created: the draft is produced asynchronously. Poll the encounter for status. */
    @PostMapping
    @ResponseStatus(HttpStatus.ACCEPTED)
    public EncounterSummary create(@Valid @RequestBody CreateEncounterRequest request) {
        return service.create(request.patientId(), request.transcript(), CurrentUser.get());
    }

    @GetMapping
    public List<EncounterSummary> list(@RequestParam(required = false) EncounterStatus status,
                                       @RequestParam(defaultValue = "100") int limit) {
        return service.list(status, limit);
    }

    @GetMapping("/{id}")
    public EncounterDetail detail(@PathVariable UUID id) {
        return service.detail(id, CurrentUser.get());
    }

    @GetMapping("/{id}/notes/{version}")
    public NoteVersionView version(@PathVariable UUID id, @PathVariable int version) {
        return service.version(id, version, CurrentUser.get());
    }

    @GetMapping("/{id}/patient-context")
    public PatientContext patientContext(@PathVariable UUID id) {
        return service.patientContext(id, CurrentUser.get());
    }

    @PostMapping("/{id}/notes")
    @ResponseStatus(HttpStatus.CREATED)
    public NoteVersionView saveEdit(@PathVariable UUID id, @Valid @RequestBody SaveNoteRequest request) {
        return service.saveEdit(id, request.baseVersion(), request.soap(), CurrentUser.get());
    }

    @PostMapping("/{id}/approve")
    public EncounterSummary approve(@PathVariable UUID id, @Valid @RequestBody ApproveRequest request) {
        return service.approve(id, request.version(), CurrentUser.get());
    }
}
