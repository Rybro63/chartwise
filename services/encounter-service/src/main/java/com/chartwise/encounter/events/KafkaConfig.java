package com.chartwise.encounter.events;

import com.chartwise.encounter.encounter.InvalidStateException;
import org.apache.kafka.common.TopicPartition;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;
import org.springframework.kafka.core.KafkaAdmin;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.listener.DeadLetterPublishingRecoverer;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.kafka.support.ExponentialBackOffWithMaxRetries;
import tools.jackson.core.JacksonException;

@Configuration
public class KafkaConfig {

    @Bean
    KafkaAdmin.NewTopics topics(@Value("${chartwise.kafka.partitions:6}") int partitions,
                            @Value("${chartwise.kafka.replicas:1}") int replicas) {
        return new KafkaAdmin.NewTopics(
                TopicBuilder.name(Topics.ENCOUNTER_CREATED).partitions(partitions).replicas(replicas).build(),
                TopicBuilder.name(Topics.NOTE_DRAFTED).partitions(partitions).replicas(replicas).build(),
                TopicBuilder.name(Topics.NOTE_APPROVED).partitions(partitions).replicas(replicas).build(),
                TopicBuilder.name(Topics.DEAD_LETTER).partitions(partitions).replicas(replicas).build());
    }

    /**
     * Retries a failing record with exponential backoff, then publishes it to the dead-letter
     * topic (same key, original topic in headers) and moves on, so one poison message cannot stall
     * a partition. Malformed payloads and state conflicts are not retried: retrying can't fix them.
     */
    @Bean
    DefaultErrorHandler kafkaErrorHandler(KafkaTemplate<String, String> template,
                                          @Value("${chartwise.kafka.max-retries:4}") int maxRetries) {
        DeadLetterPublishingRecoverer recoverer = new DeadLetterPublishingRecoverer(template,
                (record, ex) -> new TopicPartition(Topics.DEAD_LETTER, -1));
        ExponentialBackOffWithMaxRetries backOff = new ExponentialBackOffWithMaxRetries(maxRetries);
        backOff.setInitialInterval(500);
        backOff.setMultiplier(2.0);
        backOff.setMaxInterval(10_000);
        DefaultErrorHandler handler = new DefaultErrorHandler(recoverer, backOff);
        handler.addNotRetryableExceptions(JacksonException.class, IllegalArgumentException.class, InvalidStateException.class);
        return handler;
    }
}
