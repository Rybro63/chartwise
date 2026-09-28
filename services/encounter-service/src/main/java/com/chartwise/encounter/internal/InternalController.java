package com.chartwise.encounter.internal;

import com.chartwise.encounter.encounter.Dtos.DraftContext;
import com.chartwise.encounter.encounter.EncounterService;
import java.util.UUID;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
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
}
