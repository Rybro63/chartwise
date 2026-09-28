package com.chartwise.encounter.events;

import com.chartwise.encounter.config.ChartwiseProperties;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.core.ProducerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Publishes committed outbox rows to Kafka.
 *
 * <p>Rows are claimed with {@code FOR UPDATE SKIP LOCKED}, so several replicas can run the relay
 * without publishing the same row concurrently. Delivery is at-least-once: if the process dies
 * after Kafka acknowledges a send but before {@code published_at} commits, the row is sent again.
 * Consumers are idempotent on the encounter ID, which makes that duplicate harmless.
 */
@Component
public class OutboxRelay {

    private static final Logger log = LoggerFactory.getLogger(OutboxRelay.class);

    private final JdbcClient jdbc;
    private final TransactionTemplate tx;
    private final KafkaTemplate<String, String> kafka;
    private final int batchSize;
    private final Counter published;
    private final Counter failures;
    private final AtomicLong pending = new AtomicLong();
    private final AtomicLong oldestAgeSeconds = new AtomicLong();

    public OutboxRelay(JdbcClient jdbc, TransactionTemplate tx, ProducerFactory<String, String> producerFactory,
                       ChartwiseProperties properties, MeterRegistry registry) {
        this.jdbc = jdbc;
        this.tx = tx;
        // Own template with observation off: the trace context to propagate is the one captured
        // when the row was written, not the relay's scheduler thread.
        this.kafka = new KafkaTemplate<>(producerFactory);
        this.kafka.setObservationEnabled(false);
        this.batchSize = properties.outbox().batchSize();
        this.published = Counter.builder("chartwise.outbox.published").register(registry);
        this.failures = Counter.builder("chartwise.outbox.publish.failures").register(registry);
        Gauge.builder("chartwise.outbox.pending", pending, AtomicLong::get).register(registry);
        Gauge.builder("chartwise.outbox.oldest.age", oldestAgeSeconds, AtomicLong::get)
                .baseUnit("seconds")
                .register(registry);
    }

    private record Row(UUID id, String topic, String key, String payload, String traceparent) {}

    @Scheduled(fixedDelayString = "${chartwise.outbox.poll-interval}")
    public void publishPending() {
        Integer sent;
        do {
            sent = tx.execute(status -> publishBatch());
        } while (sent != null && sent == batchSize);
    }

    private int publishBatch() {
        List<Row> rows = jdbc.sql("""
                select id, topic, message_key, payload::text as payload, traceparent
                from outbox_event
                where published_at is null
                order by created_at
                limit :limit
                for update skip locked
                """)
                .param("limit", batchSize)
                .query((rs, i) -> new Row(
                        rs.getObject("id", UUID.class),
                        rs.getString("topic"),
                        rs.getString("message_key"),
                        rs.getString("payload"),
                        rs.getString("traceparent")))
                .list();

        int sent = 0;
        for (Row row : rows) {
            try {
                ProducerRecord<String, String> record = new ProducerRecord<>(row.topic(), row.key(), row.payload());
                record.headers().add("event-id", row.id().toString().getBytes(StandardCharsets.UTF_8));
                if (row.traceparent() != null) {
                    record.headers().add("traceparent", row.traceparent().getBytes(StandardCharsets.UTF_8));
                }
                kafka.send(record).get(10, TimeUnit.SECONDS);
                jdbc.sql("update outbox_event set published_at = now(), attempts = attempts + 1 where id = :id")
                        .param("id", row.id())
                        .update();
                published.increment();
                sent++;
            } catch (Exception e) {
                failures.increment();
                log.warn("Outbox publish failed for event {} to {}: {}", row.id(), row.topic(), e.getClass().getSimpleName());
                jdbc.sql("update outbox_event set attempts = attempts + 1, last_error = :error where id = :id")
                        .param("id", row.id())
                        .param("error", e.getClass().getSimpleName())
                        .update();
                // Stop here to preserve per-key ordering; the next poll retries from this row.
                break;
            }
        }
        return sent;
    }

    @Scheduled(fixedDelay = 5, timeUnit = TimeUnit.SECONDS)
    public void refreshGauges() {
        jdbc.sql("""
                select count(*) as pending,
                       coalesce(extract(epoch from now() - min(created_at)), 0)::bigint as oldest
                from outbox_event where published_at is null
                """)
                .query(rs -> {
                    pending.set(rs.getLong("pending"));
                    oldestAgeSeconds.set(rs.getLong("oldest"));
                });
    }
}
