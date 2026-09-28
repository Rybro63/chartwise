package com.chartwise.encounter.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "chartwise")
public record ChartwiseProperties(
        Jwt jwt,
        Crypto crypto,
        Fhir fhir,
        Outbox outbox,
        Retention retention,
        Seed seed,
        Slo slo) {

    public record Jwt(String secret, Duration ttl) {}

    public record Crypto(String key) {}

    public record Fhir(String baseUrl, Duration connectTimeout, Duration socketTimeout) {}

    public record Outbox(int batchSize, Duration pollInterval) {}

    public record Retention(int days, String cron) {}

    public record Seed(boolean enabled, String password, String workerPassword) {}

    public record Slo(Duration draftLatencyTarget) {}
}
