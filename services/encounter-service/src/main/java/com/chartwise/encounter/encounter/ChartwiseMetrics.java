package com.chartwise.encounter.encounter;

import com.chartwise.encounter.config.ChartwiseProperties;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import java.time.Duration;
import org.springframework.stereotype.Component;

@Component
public class ChartwiseMetrics {

    private final MeterRegistry registry;
    private final Timer draftLatency;
    private final Counter encountersCreated;
    private final Counter notesApproved;
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
        this.notesApproved = Counter.builder("chartwise.notes.approved").register(registry);
        this.notesFiled = Counter.builder("chartwise.notes.filed").register(registry);
    }

    public void draftReady(Duration sinceCreation, int safetyFlags) {
        draftLatency.record(sinceCreation);
        registry.counter("chartwise.drafts.received", "flagged", safetyFlags > 0 ? "true" : "false").increment();
    }

    public void encounterCreated() { encountersCreated.increment(); }
    public void noteApproved() { notesApproved.increment(); }
    public void noteFiled() { notesFiled.increment(); }

    public void deadLetter(String originalTopic) {
        registry.counter("chartwise.dead.letters", "original_topic", originalTopic == null ? "unknown" : originalTopic).increment();
    }
}
