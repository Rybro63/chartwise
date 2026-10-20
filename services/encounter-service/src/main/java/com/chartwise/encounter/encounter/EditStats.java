package com.chartwise.encounter.encounter;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * How much the reviewer changed the AI draft before approving it: per section and for the whole
 * note, 0 when untouched and 1 when nothing of the draft is left. This is the product's quality
 * signal from real use (how much work the draft saved), and it is numbers only, so it can go in
 * metrics and the audit trail.
 */
public final class EditStats {

    private EditStats() {}

    /** 1 - 2·LCS / (|a| + |b|) over words: 0 for identical text, 1 for nothing in common. */
    public static double editRatio(String before, String after) {
        List<String> a = words(before);
        List<String> b = words(after);
        if (a.isEmpty() && b.isEmpty()) {
            return 0.0;
        }
        return round(1.0 - 2.0 * lcs(a, b) / (a.size() + b.size()));
    }

    /** Ratios for subjective, objective, assessment, plan and the whole "note". */
    public static Map<String, Double> bySection(SoapNote aiDraft, SoapNote approved) {
        Map<String, Double> out = new LinkedHashMap<>();
        out.put("subjective", editRatio(aiDraft.subjective(), approved.subjective()));
        out.put("objective", editRatio(aiDraft.objective(), approved.objective()));
        out.put("assessment", editRatio(aiDraft.assessment(), approved.assessment()));
        out.put("plan", editRatio(aiDraft.plan(), approved.plan()));
        out.put("note", editRatio(aiDraft.render(), approved.render()));
        return out;
    }

    private static List<String> words(String text) {
        String trimmed = text == null ? "" : text.strip();
        return trimmed.isEmpty() ? List.of() : List.of(trimmed.split("\\s+"));
    }

    private static int lcs(List<String> a, List<String> b) {
        int[] prev = new int[b.size() + 1];
        int[] cur = new int[b.size() + 1];
        for (String x : a) {
            for (int j = 1; j <= b.size(); j++) {
                cur[j] = x.equals(b.get(j - 1)) ? prev[j - 1] + 1 : Math.max(prev[j], cur[j - 1]);
            }
            int[] t = prev;
            prev = cur;
            cur = t;
        }
        return prev[b.size()];
    }

    private static double round(double v) {
        return Math.round(v * 1000) / 1000.0;
    }
}
