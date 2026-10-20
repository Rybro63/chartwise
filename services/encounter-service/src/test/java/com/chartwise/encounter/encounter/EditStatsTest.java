package com.chartwise.encounter.encounter;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class EditStatsTest {

    @Test
    void identicalTextIsZero() {
        assertThat(EditStats.editRatio("Continue lisinopril 10 mg daily.", "Continue lisinopril 10 mg daily.")).isZero();
        assertThat(EditStats.editRatio("", "  ")).isZero();
    }

    @Test
    void nothingInCommonIsOne() {
        assertThat(EditStats.editRatio("Continue lisinopril", "Start amlodipine")).isEqualTo(1.0);
        assertThat(EditStats.editRatio("", "Written from scratch")).isEqualTo(1.0);
    }

    @Test
    void oneWordChangedOutOfFive() {
        // LCS 4 of 5 + 5 words: 1 - 8/10.
        assertThat(EditStats.editRatio("Continue lisinopril 10 mg daily.", "Continue lisinopril 20 mg daily.")).isEqualTo(0.2);
    }

    @Test
    void bySectionReportsEachSectionAndTheWholeNote() {
        SoapNote ai = new SoapNote("Reports headaches.", "BP 150/95.", "Hypertension.", "Continue lisinopril 10 mg.");
        SoapNote approved = new SoapNote("Reports headaches.", "BP 150/95.", "Hypertension.", "Increase lisinopril to 20 mg.");
        var ratios = EditStats.bySection(ai, approved);
        assertThat(ratios).containsOnlyKeys("subjective", "objective", "assessment", "plan", "note");
        assertThat(ratios.get("subjective")).isZero();
        assertThat(ratios.get("plan")).isGreaterThan(0.0);
        assertThat(ratios.get("note")).isBetween(0.0, ratios.get("plan"));
    }
}
