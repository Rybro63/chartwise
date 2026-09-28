package com.chartwise.encounter.fhir;

import java.time.Instant;
import java.util.UUID;

public record FiledNote(UUID encounterId, String patientId, String approvedBy, Instant approvedAt, String text) {}
