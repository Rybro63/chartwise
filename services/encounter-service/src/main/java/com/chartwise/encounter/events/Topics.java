package com.chartwise.encounter.events;

public final class Topics {
    public static final String ENCOUNTER_CREATED = "encounter.created";
    public static final String NOTE_DRAFTED = "note.drafted";
    public static final String NOTE_APPROVED = "note.approved";
    /** Shared dead-letter topic. The original topic travels in the kafka_dlt-original-topic header. */
    public static final String DEAD_LETTER = "chartwise.dead-letter";

    private Topics() {}
}
