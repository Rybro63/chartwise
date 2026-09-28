package com.chartwise.encounter.fhir;

public class FhirUnavailableException extends RuntimeException {
    public FhirUnavailableException(String message, Throwable cause) {
        super(message, cause);
    }
}
