package com.chartwise.encounter.fhir;

import java.util.List;
import java.util.Optional;

/** All access to the FHIR patient record server goes through this interface. */
public interface FhirGateway {

    List<PatientSummary> searchPatients(String name, int count);

    Optional<PatientSummary> findPatient(String patientId);

    PatientContext loadContext(String patientId);

    /**
     * Writes an approved note as a DocumentReference. Idempotent: a second call for the same
     * encounter returns the existing resource's ID instead of creating a duplicate.
     */
    String fileNote(FiledNote note);
}
