package com.chartwise.encounter;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.UUID;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.http.MediaType;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.KafkaHeaders;
import tools.jackson.databind.JsonNode;

@ExtendWith(OutputCaptureExtension.class)
class EncounterFlowIntegrationTest extends IntegrationTestBase {

    @Autowired KafkaTemplate<String, String> kafkaTemplate;

    @Test
    void fullFlowFromTranscriptToFiledNote_withoutLeakingPhiToLogs(CapturedOutput output) throws Exception {
        String scribe = token("scribe");
        String clinician = token("clinician");

        // 1. Upload a transcript.
        UUID id = createEncounter(scribe);

        // 2. The outbox relay publishes encounter.created, keyed by encounter ID, with no PHI.
        ConsumerRecord<String, String> created = awaitRecord("encounter.created", id.toString());
        assertThat(created.value()).contains(id.toString()).doesNotContain(CANARY_TRANSCRIPT).doesNotContain(CANARY_PATIENT_NAME);
        assertThat(created.headers().lastHeader("event-id")).isNotNull();

        // 3. The worker can fetch the draft context through the internal API; clinicians cannot.
        String worker = token("note-worker");
        JsonNode ctx = read(mvc.perform(get("/internal/encounters/{id}/draft-context", id).header("Authorization", worker))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString());
        assertThat(ctx.get("transcript").asString()).isEqualTo(CANARY_TRANSCRIPT);
        assertThat(ctx.has("patientName")).isFalse();
        mvc.perform(get("/internal/encounters/{id}/draft-context", id).header("Authorization", clinician))
                .andExpect(status().isForbidden());

        // 4. The worker's note.drafted is delivered twice; only one version 1 may result.
        String drafted = noteDrafted(id);
        kafkaTemplate.send("note.drafted", id.toString(), drafted).get();
        kafkaTemplate.send("note.drafted", id.toString(), drafted).get();
        awaitStatus(clinician, id, "IN_REVIEW");
        Thread.sleep(500); // let the duplicate be consumed too
        assertThat(countVersions(id)).isEqualTo(1);
        assertThat(jdbc.sql("select count(*) from processed_message where consumer = 'note-drafted' and message_key = :k")
                .param("k", id.toString()).query(Long.class).single()).isEqualTo(1);

        JsonNode detail = read(mvc.perform(get("/api/encounters/{id}", id).header("Authorization", clinician))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString());
        assertThat(detail.get("transcript").asString()).isEqualTo(CANARY_TRANSCRIPT);
        assertThat(detail.get("latest").get("authorType").asString()).isEqualTo("AI");
        assertThat(detail.get("draftSafetyFlags").size()).isEqualTo(1);

        // 5. The scribe edits: a new version, attributed to the scribe.
        mvc.perform(post("/api/encounters/{id}/notes", id).header("Authorization", scribe)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json.writeValueAsString(Map.of("baseVersion", 1, "soap", soap(CANARY_NOTE + " edited")))))
                .andExpect(status().isCreated());

        // A save based on the old version is rejected rather than silently overwriting.
        mvc.perform(post("/api/encounters/{id}/notes", id).header("Authorization", clinician)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json.writeValueAsString(Map.of("baseVersion", 1, "soap", soap("conflicting edit")))))
                .andExpect(status().isConflict());

        // 6. Only clinicians approve, and only the latest version.
        mvc.perform(post("/api/encounters/{id}/approve", id).header("Authorization", scribe)
                        .contentType(MediaType.APPLICATION_JSON).content("{\"version\":2}"))
                .andExpect(status().isForbidden());
        mvc.perform(post("/api/encounters/{id}/approve", id).header("Authorization", clinician)
                        .contentType(MediaType.APPLICATION_JSON).content("{\"version\":1}"))
                .andExpect(status().isConflict());
        mvc.perform(post("/api/encounters/{id}/approve", id).header("Authorization", clinician)
                        .contentType(MediaType.APPLICATION_JSON).content("{\"version\":2}"))
                .andExpect(status().isOk());

        // 7. note.approved -> FHIR DocumentReference -> FILED.
        awaitStatus(clinician, id, "FILED");
        assertThat(fhir.filed).anySatisfy(n -> {
            assertThat(n.encounterId()).isEqualTo(id);
            assertThat(n.text()).contains(CANARY_NOTE + " edited").startsWith("SUBJECTIVE");
        });

        // 8. Every step is in the audit trail, attributed to the right actor.
        List<String> actions = jdbc.sql("select actor || ':' || action from audit_event where encounter_id = :id order by id")
                .param("id", id).query(String.class).list();
        assertThat(actions).contains("scribe:ENCOUNTER_CREATED", "note-worker:DRAFT_CONTEXT_READ", "note-worker:DRAFT_RECEIVED",
                "clinician:ENCOUNTER_VIEWED", "scribe:NOTE_EDITED", "clinician:NOTE_APPROVED", "system:NOTE_FILED");

        // 9. PHI is encrypted at rest.
        byte[] stored = jdbc.sql("select transcript_ciphertext from encounter where id = :id").param("id", id)
                .query(byte[].class).single();
        assertThat(new String(stored, StandardCharsets.ISO_8859_1)).doesNotContain("PHI-CANARY");

        // 10. And none of it reached the logs.
        assertThat(output.getAll())
                .doesNotContain(CANARY_TRANSCRIPT)
                .doesNotContain("PHI-CANARY")
                .doesNotContain(CANARY_PATIENT_NAME)
                .doesNotContain(CANARY_NOTE);
    }

    @Test
    void deadLetteredDraftBecomesVisibleAndCanBeRetried() throws Exception {
        String scribe = token("scribe");
        String admin = token("admin");
        UUID id = createEncounter(scribe);

        // Simulate the worker giving up on this encounter.
        ProducerRecord<String, String> dead = new ProducerRecord<>("chartwise.dead-letter", id.toString(), "{}");
        dead.headers().add(KafkaHeaders.DLT_ORIGINAL_TOPIC, "encounter.created".getBytes(StandardCharsets.UTF_8));
        dead.headers().add(KafkaHeaders.DLT_EXCEPTION_FQCN, "chartwise.worker.GatewayUnavailable".getBytes(StandardCharsets.UTF_8));
        dead.headers().add(KafkaHeaders.DLT_EXCEPTION_MESSAGE, "circuit open after 5 attempts".getBytes(StandardCharsets.UTF_8));
        kafkaTemplate.send(dead).get();

        await().atMost(Duration.ofSeconds(20)).untilAsserted(() -> assertThat(dbStatus(id)).isEqualTo("DRAFT_FAILED"));
        String reason = jdbc.sql("select failure_reason from encounter where id = :id").param("id", id).query(String.class).single();
        assertThat(reason).isEqualTo("GatewayUnavailable: circuit open after 5 attempts");

        // Clinical users can't reach admin endpoints; admins can't read PHI endpoints.
        mvc.perform(post("/api/admin/encounters/{id}/retry", id).header("Authorization", scribe)).andExpect(status().isForbidden());
        mvc.perform(get("/api/encounters/{id}", id).header("Authorization", admin)).andExpect(status().isForbidden());

        mvc.perform(post("/api/admin/encounters/{id}/retry", id).header("Authorization", admin)).andExpect(status().isOk());
        assertThat(dbStatus(id)).isEqualTo("DRAFTING");
        ConsumerRecord<String, String> redrive = awaitRecord("encounter.created", id.toString(), r -> json.readTree(r.value()).get("attempt").asInt() == 2);
        assertThat(redrive).isNotNull();

        // A draft for the retried attempt is accepted.
        kafkaTemplate.send("note.drafted", id.toString(), noteDrafted(id)).get();
        await().atMost(Duration.ofSeconds(20)).untilAsserted(() -> assertThat(dbStatus(id)).isEqualTo("IN_REVIEW"));
    }

    @Test
    void failedFilingIsRetriedThenDeadLetteredThenRedrivable() throws Exception {
        String scribe = token("scribe");
        String clinician = token("clinician");
        String admin = token("admin");
        UUID id = createEncounter(scribe);
        kafkaTemplate.send("note.drafted", id.toString(), noteDrafted(id)).get();
        await().atMost(Duration.ofSeconds(20)).untilAsserted(() -> assertThat(dbStatus(id)).isEqualTo("IN_REVIEW"));

        fhir.failFiling = true;
        try {
            mvc.perform(post("/api/encounters/{id}/approve", id).header("Authorization", clinician)
                            .contentType(MediaType.APPLICATION_JSON).content("{\"version\":1}"))
                    .andExpect(status().isOk());
            await().atMost(Duration.ofSeconds(30)).untilAsserted(() -> assertThat(
                    jdbc.sql("select failure_reason from encounter where id = :id").param("id", id).query(String.class).optional())
                    .hasValueSatisfying(r -> assertThat(r).contains("FhirUnavailableException")));
            assertThat(dbStatus(id)).isEqualTo("APPROVED");
        } finally {
            fhir.failFiling = false;
        }

        mvc.perform(post("/api/admin/encounters/{id}/retry", id).header("Authorization", admin)).andExpect(status().isOk());
        await().atMost(Duration.ofSeconds(20)).untilAsserted(() -> assertThat(dbStatus(id)).isEqualTo("FILED"));
    }

    @Test
    void unauthenticatedRequestsAreRejected() throws Exception {
        mvc.perform(get("/api/encounters")).andExpect(status().isUnauthorized());
        mvc.perform(get("/api/encounters").header("Authorization", "Bearer not-a-jwt")).andExpect(status().isUnauthorized());
        mvc.perform(post("/api/auth/login").contentType(MediaType.APPLICATION_JSON)
                .content("{\"username\":\"clinician\",\"password\":\"wrong\"}")).andExpect(status().isUnauthorized());
    }

    // ---- helpers -----------------------------------------------------------------------------

    private UUID createEncounter(String auth) throws Exception {
        String body = mvc.perform(post("/api/encounters").header("Authorization", auth)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json.writeValueAsString(Map.of("patientId", PATIENT_ID, "transcript", CANARY_TRANSCRIPT))))
                .andExpect(status().isAccepted())
                .andReturn().getResponse().getContentAsString();
        JsonNode node = read(body);
        assertThat(node.get("status").asString()).isEqualTo("DRAFTING");
        return UUID.fromString(node.get("id").asString());
    }

    private String dbStatus(UUID id) {
        return jdbc.sql("select status from encounter where id = :id").param("id", id).query(String.class).single();
    }

    private void awaitStatus(String auth, UUID id, String expected) {
        await().atMost(Duration.ofSeconds(30)).untilAsserted(() -> assertThat(dbStatus(id)).isEqualTo(expected));
    }

    private long countVersions(UUID id) {
        return jdbc.sql("select count(*) from note_version where encounter_id = :id").param("id", id).query(Long.class).single();
    }

    private Map<String, String> soap(String plan) {
        return Map.of("subjective", "Patient reports " + CANARY_NOTE, "objective", "BP 150/95",
                "assessment", "Essential hypertension", "plan", plan);
    }

    private String noteDrafted(UUID id) {
        return json.writeValueAsString(Map.of(
                "eventId", UUID.randomUUID().toString(),
                "eventType", "NoteDrafted",
                "encounterId", id.toString(),
                "soap", soap("Continue lisinopril 10 mg daily. " + CANARY_NOTE),
                "safetyFlags", List.of(Map.of("code", "UNSUPPORTED_MEDICATION", "severity", "high",
                        "subject", "metformin", "message", "not in record or transcript", "section", "plan")),
                "model", "fake-llm",
                "promptVersion", "soap-v1",
                "gatewayAttempts", 1,
                "occurredAt", Instant.now().toString()));
    }

    private ConsumerRecord<String, String> awaitRecord(String topic, String key) {
        return awaitRecord(topic, key, r -> true);
    }

    private ConsumerRecord<String, String> awaitRecord(String topic, String key,
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
}
