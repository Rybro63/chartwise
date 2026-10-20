package com.chartwise.encounter;

import static org.awaitility.Awaitility.await;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.chartwise.encounter.fhir.FhirGateway;
import com.chartwise.encounter.fhir.FiledNote;
import com.chartwise.encounter.fhir.PatientContext;
import com.chartwise.encounter.fhir.PatientSummary;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.UUID;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.common.serialization.StringDeserializer;
import java.util.Optional;
import java.util.concurrent.CopyOnWriteArrayList;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.kafka.KafkaContainer;
import org.testcontainers.postgresql.PostgreSQLContainer;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Boots the full service against real PostgreSQL and Kafka (Testcontainers). The containers are
 * started once per JVM and shared by every test class, so the Spring context is cached too.
 */
@SpringBootTest(properties = {
        "chartwise.outbox.poll-interval=100ms",
        "chartwise.kafka.max-retries=1",
        "management.tracing.export.enabled=false",
        "logging.level.com.chartwise=DEBUG",
})
@AutoConfigureMockMvc
@Import(IntegrationTestBase.StubFhirConfig.class)
public abstract class IntegrationTestBase {

    /** Synthetic canary values. If any of them appears in log output, PHI is leaking. */
    public static final String CANARY_PATIENT_NAME = "Zyxwvut Canarypatient";
    public static final String CANARY_TRANSCRIPT = "PHI-CANARY-TRANSCRIPT-7f3a9c patient reports chest tightness";
    public static final String CANARY_NOTE = "PHI-CANARY-NOTE-41b8e2";
    public static final String PATIENT_ID = "synthetic-patient-1";

    @ServiceConnection
    static final PostgreSQLContainer postgres = new PostgreSQLContainer("postgres:17-alpine");

    @ServiceConnection
    static final KafkaContainer kafka = new KafkaContainer("apache/kafka:4.1.0");

    static {
        postgres.start();
        kafka.start();
    }

    @Autowired protected MockMvc mvc;
    @Autowired protected JsonMapper json;
    @Autowired protected JdbcClient jdbc;
    @Autowired protected StubFhirGateway fhir;

    protected String token(String username) throws Exception {
        String password = switch (username) {
            case "note-worker" -> "chartwise-worker-dev";
            case "transcriber" -> "chartwise-transcriber-dev";
            default -> "chartwise-dev";
        };
        String body = mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json.writeValueAsString(Map.of("username", username, "password", password))))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        return "Bearer " + json.readTree(body).get("token").asString();
    }

    protected ConsumerRecord<String, String> awaitRecord(String topic, String key,
                                                       java.util.function.Predicate<ConsumerRecord<String, String>> match) {
        Properties props = new Properties();
        props.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, kafka.getBootstrapServers());
        props.put(ConsumerConfig.GROUP_ID_CONFIG, "test-" + UUID.randomUUID());
        props.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        props.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        props.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(props)) {
            consumer.subscribe(List.of(topic));
            List<ConsumerRecord<String, String>> found = new ArrayList<>();
            await().atMost(Duration.ofSeconds(30)).until(() -> {
                consumer.poll(Duration.ofMillis(500)).forEach(r -> {
                    if (key.equals(r.key()) && match.test(r)) {
                        found.add(r);
                    }
                });
                return !found.isEmpty();
            });
            return found.getFirst();
        }
    }

    protected JsonNode read(String body) {
        return json.readTree(body);
    }

    @TestConfiguration
    static class StubFhirConfig {
        @Bean
        @Primary
        StubFhirGateway stubFhirGateway() {
            return new StubFhirGateway();
        }
    }

    /** In-memory FHIR server stand-in holding one synthetic patient. */
    public static class StubFhirGateway implements FhirGateway {
        public final List<FiledNote> filed = new CopyOnWriteArrayList<>();
        public volatile boolean failFiling = false;

        @Override
        public List<PatientSummary> searchPatients(String name, int count) {
            return List.of(new PatientSummary(PATIENT_ID, CANARY_PATIENT_NAME, "female", "1961-04-02"));
        }

        @Override
        public Optional<PatientSummary> findPatient(String patientId) {
            return PATIENT_ID.equals(patientId) ? Optional.of(searchPatients(null, 1).getFirst()) : Optional.empty();
        }

        @Override
        public PatientContext loadContext(String patientId) {
            return new PatientContext(64, "female", List.of("Essential hypertension (disorder)"),
                    List.of("lisinopril 10 MG Oral Tablet"), List.of());
        }

        @Override
        public String fileNote(FiledNote note) {
            if (failFiling) {
                throw new com.chartwise.encounter.fhir.FhirUnavailableException("FHIR server returned HTTP 503", null);
            }
            filed.add(note);
            return "doc-" + note.encounterId();
        }
    }
}
