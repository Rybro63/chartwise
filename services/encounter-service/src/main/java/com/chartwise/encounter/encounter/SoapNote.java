package com.chartwise.encounter.encounter;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

public record SoapNote(
        @NotNull @Size(max = 50_000) String subjective,
        @NotNull @Size(max = 50_000) String objective,
        @NotNull @Size(max = 50_000) String assessment,
        @NotNull @Size(max = 50_000) String plan) {

    /** Plain-text rendering used for the FHIR DocumentReference attachment. */
    public String render() {
        return "SUBJECTIVE\n" + subjective.strip() + "\n\n"
                + "OBJECTIVE\n" + objective.strip() + "\n\n"
                + "ASSESSMENT\n" + assessment.strip() + "\n\n"
                + "PLAN\n" + plan.strip() + "\n";
    }
}
