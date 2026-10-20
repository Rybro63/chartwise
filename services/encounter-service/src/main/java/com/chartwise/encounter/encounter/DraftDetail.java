package com.chartwise.encounter.encounter;

import java.util.List;

/**
 * What the AI draft was built from (prompt soap-v2): the transcript lines each statement came
 * from, and the model's own list of the medications it mentions. Stored encrypted with version 1,
 * because statement text is PHI. Drafts from soap-v1 have none.
 */
public record DraftDetail(List<Statement> evidence, List<DraftMedication> medications) {

    /**
     * One sentence or bullet of the draft, with the 1-based transcript lines it cites. absence marks
     * a statement that something wasn't documented, which needs no source. It is a Boolean, null in
     * events and stored drafts from before it existed: Jackson 3 rejects a missing primitive, which
     * would dead-letter those events and make those drafts unreadable.
     */
    public record Statement(String section, String text, List<Integer> lines, boolean fromRecord, Boolean absence) {}

    /** status is continued, started, changed, stopped or mentioned; dose is as stated in the visit. */
    public record DraftMedication(String name, String status, String dose) {}
}
