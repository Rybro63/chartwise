package com.chartwise.encounter.events;

import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * Records that a consumer has handled a message key. Must run in the same transaction as the
 * consumer's effect: if the effect rolls back, so does the record, and a redelivery is processed.
 */
@Component
public class IdempotencyGuard {

    private final JdbcClient jdbc;
    private final MeterRegistry registry;

    public IdempotencyGuard(JdbcClient jdbc, MeterRegistry registry) {
        this.jdbc = jdbc;
        this.registry = registry;
    }

    /** @return true the first time this (consumer, key) pair is seen, false for a duplicate. */
    @Transactional(propagation = Propagation.MANDATORY)
    public boolean firstDelivery(String consumer, String key) {
        int inserted = jdbc.sql("""
                insert into processed_message (consumer, message_key) values (:consumer, :key)
                on conflict do nothing
                """)
                .param("consumer", consumer)
                .param("key", key)
                .update();
        return inserted == 1;
    }

    public void recordDuplicate(String consumer) {
        registry.counter("chartwise.events.duplicates", "consumer", consumer).increment();
    }
}
