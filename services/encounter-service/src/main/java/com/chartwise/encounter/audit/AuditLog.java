package com.chartwise.encounter.audit;

import com.chartwise.encounter.security.CurrentUser;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;

/**
 * Append-only audit trail. There is intentionally no update or delete method; the table also
 * rejects UPDATE, DELETE and TRUNCATE with a trigger (see V1__init.sql).
 *
 * <p>Detail maps must hold identifiers and enum-like values only, never PHI.
 */
@Component
public class AuditLog {

    private final JdbcClient jdbc;
    private final JsonMapper json;

    public AuditLog(JdbcClient jdbc, JsonMapper json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    public void record(CurrentUser actor, AuditAction action, UUID encounterId, Integer noteVersion, Map<String, ?> detail) {
        jdbc.sql("""
                insert into audit_event (actor, actor_role, action, encounter_id, note_version, detail)
                values (:actor, :role, :action, :encounterId, :noteVersion, cast(:detail as jsonb))
                """)
                .param("actor", actor.username())
                .param("role", actor.role().name())
                .param("action", action.name())
                .param("encounterId", encounterId)
                .param("noteVersion", noteVersion)
                .param("detail", json.writeValueAsString(detail == null ? Map.of() : detail))
                .update();
    }

    public void record(CurrentUser actor, AuditAction action, UUID encounterId) {
        record(actor, action, encounterId, null, Map.of());
    }

    public List<AuditEntry> find(UUID encounterId, int limit) {
        String where = encounterId == null ? "" : "where encounter_id = :encounterId";
        var spec = jdbc.sql("""
                select id, occurred_at, actor, actor_role, action, encounter_id, note_version, detail::text as detail
                from audit_event %s
                order by id desc
                limit :limit
                """.formatted(where))
                .param("limit", limit);
        if (encounterId != null) {
            spec = spec.param("encounterId", encounterId);
        }
        return spec.query((rs, i) -> new AuditEntry(
                        rs.getLong("id"),
                        rs.getObject("occurred_at", OffsetDateTime.class).toInstant(),
                        rs.getString("actor"),
                        rs.getString("actor_role"),
                        rs.getString("action"),
                        rs.getObject("encounter_id", UUID.class),
                        (Integer) rs.getObject("note_version"),
                        rs.getString("detail")))
                .list();
    }

    public record AuditEntry(long id, Instant occurredAt, String actor, String actorRole, String action,
                             UUID encounterId, Integer noteVersion, String detail) {}
}
