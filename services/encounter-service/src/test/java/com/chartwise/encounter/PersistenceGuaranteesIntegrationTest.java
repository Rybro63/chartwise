package com.chartwise.encounter;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.chartwise.encounter.encounter.Encounter;
import com.chartwise.encounter.encounter.EncounterRepository;
import com.chartwise.encounter.events.Events;
import com.chartwise.encounter.events.OutboxWriter;
import com.chartwise.encounter.retention.RetentionJob;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.transaction.IllegalTransactionStateException;
import org.springframework.transaction.support.TransactionTemplate;

class PersistenceGuaranteesIntegrationTest extends IntegrationTestBase {

    @Autowired TransactionTemplate tx;
    @Autowired EncounterRepository encounters;
    @Autowired OutboxWriter outbox;
    @Autowired RetentionJob retention;

    @Test
    void encounterAndOutboxEventRollBackTogether() {
        UUID[] id = new UUID[1];
        assertThatThrownBy(() -> tx.executeWithoutResult(status -> {
            Encounter e = encounters.save(new Encounter(PATIENT_ID, "name", "transcript", "scribe"));
            id[0] = e.getId();
            outbox.append(e.getId(), "encounter.created", Events.EncounterCreated.of(e.getId(), PATIENT_ID, 1));
            throw new IllegalStateException("simulated failure after both writes");
        })).isInstanceOf(IllegalStateException.class);

        assertThat(jdbc.sql("select count(*) from encounter where id = :id").param("id", id[0]).query(Long.class).single()).isZero();
        assertThat(jdbc.sql("select count(*) from outbox_event where aggregate_id = :id").param("id", id[0]).query(Long.class).single()).isZero();
    }

    @Test
    void outboxWriterRefusesToRunOutsideATransaction() {
        UUID id = UUID.randomUUID();
        assertThatThrownBy(() -> outbox.append(id, "encounter.created", Events.EncounterCreated.of(id, PATIENT_ID, 1)))
                .isInstanceOf(IllegalTransactionStateException.class);
    }

    @Test
    void auditLogIsAppendOnly() {
        jdbc.sql("insert into audit_event (actor, actor_role, action) values ('test', 'ADMIN', 'AUDIT_VIEWED')").update();
        assertThatThrownBy(() -> jdbc.sql("update audit_event set actor = 'tampered'").update())
                .hasMessageContaining("append-only");
        assertThatThrownBy(() -> jdbc.sql("delete from audit_event").update())
                .hasMessageContaining("append-only");
        assertThatThrownBy(() -> jdbc.sql("truncate audit_event").update())
                .hasMessageContaining("append-only");
    }

    @Test
    void retentionPurgesExpiredTerminalEncountersButKeepsTheAuditTrail() {
        UUID expired = insertEncounter("FILED", "now() - interval '400 days'");
        UUID recent = insertEncounter("FILED", "now()");
        UUID inReview = insertEncounter("IN_REVIEW", "now() - interval '400 days'");
        jdbc.sql("insert into audit_event (actor, actor_role, action, encounter_id) values ('scribe', 'SCRIBE', 'ENCOUNTER_CREATED', :id)")
                .param("id", expired).update();

        RetentionJob.Result result = retention.purge();

        assertThat(result.encountersPurged()).isGreaterThanOrEqualTo(1);
        assertThat(exists(expired)).isFalse();
        assertThat(exists(recent)).isTrue();
        assertThat(exists(inReview)).as("work in progress is never purged").isTrue();
        assertThat(jdbc.sql("select action from audit_event where encounter_id = :id order by id").param("id", expired)
                .query(String.class).list()).containsExactly("ENCOUNTER_CREATED", "ENCOUNTER_PURGED");
    }

    private UUID insertEncounter(String status, String updatedAt) {
        Encounter e = tx.execute(s -> encounters.save(new Encounter(PATIENT_ID, "name", "transcript", "scribe")));
        jdbc.sql("update encounter set status = :status, updated_at = " + updatedAt + " where id = :id")
                .param("status", status).param("id", e.getId()).update();
        return e.getId();
    }

    private boolean exists(UUID id) {
        return jdbc.sql("select count(*) from encounter where id = :id").param("id", id).query(Long.class).single() > 0;
    }
}
