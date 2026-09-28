package com.chartwise.encounter.fhir;

import java.util.List;

/**
 * The slice of a patient's record the note worker needs. Deliberately excludes name, address and
 * identifiers (HIPAA minimum necessary): the LLM gets age and sex, not who the patient is.
 */
public record PatientContext(
        Integer ageYears,
        String gender,
        List<String> conditions,
        List<String> medications,
        List<String> allergies) {}
