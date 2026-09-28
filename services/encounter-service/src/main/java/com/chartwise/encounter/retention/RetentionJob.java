package com.chartwise.encounter.retention;

import com.chartwise.encounter.audit.AuditAction;
import com.chartwise.encounter.audit.AuditLog;
import com.chartwise.encounter.config.ChartwiseProperties;
import com.chartwise.encounter.encounter.EncounterRepository;
import com.chartwise.encounter.encounter.EncounterStatus;
import com.chartwise.encounter.security.CurrentUser;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.PageRequest;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Deletes local working copies once they have outlived the retention period.
 *
 * <ul>
 *   <li>Only terminal encounters are purged: FILED (the signed note lives on in FHIR) and
 *       DRAFT_FAILED. Anything still in someone's workflow is kept.</li>
 *   <li>Deleting an encounter cascades to its note versions, which removes the transcript and every
 *       draft.</li>
 *   <li>The audit log is not touched. It holds IDs only, and it is append-only by design; each purge
 *       adds an ENCOUNTER_PURGED entry.</li>
 * </ul>
 */
@Component
public class RetentionJob {

    private static final Logger log = LoggerFactory.getLogger(RetentionJob.class);
    private static final int BATCH = 200;

    private final EncounterRepository encounters;
    private final JdbcClient jdbc;
    private final AuditLog audit;
    private final TransactionTemplate tx;
    private final Duration retention;

    public RetentionJob(EncounterRepository encounters, JdbcClient jdbc, AuditLog audit, TransactionTemplate tx,
                        ChartwiseProperties properties) {
        this.encounters = encounters;
        this.jdbc = jdbc;
        this.audit = audit;
        this.tx = tx;
        this.retention = Duration.ofDays(properties.retention().days());
    }

    public record Result(int encountersPurged, int outboxRowsPurged, int processedMessagesPurged, Instant cutoff) {}

    @Scheduled(cron = "${chartwise.retention.cron}")
    public void scheduled() {
        purge();
    }

    public Result purge() {
        Instant cutoff = Instant.now().minus(retention);
        int purged = 0;
        List<UUID> batch;
        do {
            batch = encounters.findPurgeable(List.of(EncounterStatus.FILED, EncounterStatus.DRAFT_FAILED), cutoff,
                    PageRequest.of(0, BATCH));
            List<UUID> ids = batch;
            tx.executeWithoutResult(status -> {
                for (UUID id : ids) {
                    jdbc.sql("delete from encounter where id = :id").param("id", id).update();
                    audit.record(CurrentUser.SYSTEM, AuditAction.ENCOUNTER_PURGED, id, null,
                            Map.of("retentionDays", retention.toDays()));
                }
            });
            purged += batch.size();
        } while (batch.size() == BATCH);

        Integer outbox = tx.execute(s -> jdbc.sql("delete from outbox_event where published_at < :cutoff")
                .param("cutoff", java.sql.Timestamp.from(cutoff)).update());
        Integer processed = tx.execute(s -> jdbc.sql("delete from processed_message where processed_at < :cutoff")
                .param("cutoff", java.sql.Timestamp.from(cutoff)).update());

        Result result = new Result(purged, outbox == null ? 0 : outbox, processed == null ? 0 : processed, cutoff);
        log.info("Retention purge: {} encounters, {} outbox rows, {} processed-message rows older than {}",
                result.encountersPurged(), result.outboxRowsPurged(), result.processedMessagesPurged(), cutoff);
        return result;
    }
}
