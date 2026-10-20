package com.chartwise.encounter.encounter;

import com.chartwise.encounter.config.ChartwiseProperties;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.DistributionSummary;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import java.time.Duration;
import java.util.Map;
import org.springframework.stereotype.Component;

@Component
public class ChartwiseMetrics {

    private final MeterRegistry registry;
    private final Timer draftLatency;
    private final Counter encountersCreated;
    private final Counter notesFiled;

    public ChartwiseMetrics(MeterRegistry registry, ChartwiseProperties properties) {
        this.registry = registry;
        Duration target = properties.slo().draftLatencyTarget();
        // End-to-end time from encounter creation to a reviewable draft: the SLO metric.
        // The SLO target is always one of the bucket boundaries so the alert query is exact.
        this.draftLatency = Timer.builder("chartwise.note.draft.latency")
                .description("Time from encounter creation to the AI draft being ready for review")
                .serviceLevelObjectives(Duration.ofSeconds(1), Duration.ofSeconds(2), Duration.ofSeconds(5),
                        Duration.ofSeconds(10), Duration.ofSeconds(15), Duration.ofSeconds(20), target,
                        Duration.ofSeconds(45), Duration.ofSeconds(60), Duration.ofSeconds(120), Duration.ofSeconds(300))
                .register(registry);
        this.encountersCreated = Counter.builder("chartwise.encounters.submitted").register(registry);
        this.notesFiled = Counter.builder("chartwise.notes.filed").register(registry);
    }

    public void draftReady(Duration sinceCreation, int safetyFlags) {
        draftLatency.record(sinceCreation);
        registry.counter("chartwise.drafts.received", "flagged", safetyFlags > 0 ? "true" : "false").increment();
    }

    public void encounterCreated() { encountersCreated.increment(); }

    /** From upload to transcript, and how much audio that was. */
    public void transcribed(Duration sinceUpload, Double audioSeconds) {
        Timer.builder("chartwise.transcription.latency")
                .description("Time from a recording's upload to its transcript")
                .serviceLevelObjectives(Duration.ofSeconds(10), Duration.ofSeconds(30), Duration.ofSeconds(60),
                        Duration.ofSeconds(120), Duration.ofSeconds(300), Duration.ofSeconds(600))
                .register(registry).record(sinceUpload);
        if (audioSeconds != null) {
            registry.counter("chartwise.transcribed.audio.seconds").increment(audioSeconds);
        }
    }
    /**
     * Records an approval and how much the reviewer changed the AI draft first (EditStats: 0 =
     * approved as drafted, 1 = rewritten), per section and for the whole note.
     */
    public void noteApproved(Map<String, Double> editRatios) {
        boolean edited = editRatios.getOrDefault("note", 0.0) > 0;
        registry.counter("chartwise.notes.approved", "edited", String.valueOf(edited)).increment();
        editRatios.forEach((section, ratio) -> DistributionSummary.builder("chartwise.note.edit.ratio")
                .description("Share of the AI draft changed before approval (0 untouched, 1 rewritten)")
                .tag("section", section)
                // 0.001 catches "approved as drafted" (boundaries must be above 0).
                .serviceLevelObjectives(0.001, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0)
                .register(registry)
                .record(ratio));
    }
    public void noteFiled() { notesFiled.increment(); }

    public void deadLetter(String originalTopic) {
        registry.counter("chartwise.dead.letters", "original_topic", originalTopic == null ? "unknown" : originalTopic).increment();
    }
}
