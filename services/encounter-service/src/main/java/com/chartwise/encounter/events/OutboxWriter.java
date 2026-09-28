package com.chartwise.encounter.events;

import io.micrometer.tracing.Span;
import io.micrometer.tracing.Tracer;
import io.micrometer.tracing.propagation.Propagator;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.json.JsonMapper;

/**
 * Writes an event to the outbox table. {@link Propagation#MANDATORY} makes it impossible to call
 * this outside the transaction that performs the state change, which is the whole point of the
 * pattern: the row and the event commit or roll back together.
 */
@Component
public class OutboxWriter {

    private final JdbcClient jdbc;
    private final JsonMapper json;
    private final Tracer tracer;
    private final Propagator propagator;

    public OutboxWriter(JdbcClient jdbc, JsonMapper json, Tracer tracer, Propagator propagator) {
        this.jdbc = jdbc;
        this.json = json;
        this.tracer = tracer;
        this.propagator = propagator;
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void append(UUID aggregateId, String topic, Object payload) {
        jdbc.sql("""
                insert into outbox_event (id, aggregate_id, topic, message_key, payload, traceparent)
                values (:id, :aggregateId, :topic, :key, cast(:payload as jsonb), :traceparent)
                """)
                .param("id", UUID.randomUUID())
                .param("aggregateId", aggregateId)
                .param("topic", topic)
                .param("key", aggregateId.toString())
                .param("payload", json.writeValueAsString(payload))
                .param("traceparent", currentTraceparent())
                .update();
    }

    /** Captured so the consumer's spans join the trace of the request that caused the event. */
    private String currentTraceparent() {
        Span span = tracer.currentSpan();
        if (span == null) {
            return null;
        }
        Map<String, String> carrier = new HashMap<>();
        propagator.inject(span.context(), carrier, Map::put);
        return carrier.get("traceparent");
    }
}
