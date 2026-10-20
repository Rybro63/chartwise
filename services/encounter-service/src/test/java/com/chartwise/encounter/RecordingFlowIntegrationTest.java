package com.chartwise.encounter;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;
import java.util.UUID;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.KafkaHeaders;
import org.springframework.mock.web.MockMultipartFile;
import tools.jackson.databind.JsonNode;

/** A visit uploaded as audio: recording -> transcriber (simulated) -> transcript -> drafting. */
class RecordingFlowIntegrationTest extends IntegrationTestBase {

    // Not real audio: the encounter service stores and returns bytes without decoding them.
    private static final byte[] AUDIO = ("RIFF-synthetic-recording-" + CANARY_TRANSCRIPT).getBytes(StandardCharsets.UTF_8);

    @Autowired KafkaTemplate<String, String> kafkaTemplate;

    @Test
    void recordingIsTranscribedThenDraftedAndTheAudioIsDeleted() throws Exception {
        String scribe = token("scribe");
        String transcriber = token("transcriber");
        String clinician = token("clinician");

        UUID id = upload(scribe);
        assertThat(dbStatus(id)).isEqualTo("TRANSCRIBING");
        // Stored encrypted.
        byte[] stored = jdbc.sql("select audio_ciphertext from recording where encounter_id = :id").param("id", id)
                .query(byte[].class).single();
        assertThat(new String(stored, StandardCharsets.ISO_8859_1)).doesNotContain("RIFF-synthetic-recording");

        // The event carries IDs only.
        ConsumerRecord<String, String> recorded = awaitRecord("encounter.recorded", id.toString(), r -> true);
        assertThat(recorded.value()).contains(id.toString()).doesNotContain(CANARY_TRANSCRIPT);

        // The transcriber reads the recording and the record's medication names; clinicians can't.
        JsonNode info = read(mvc.perform(get("/internal/encounters/{id}/recording", id).header("Authorization", transcriber))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString());
        assertThat(info.get("status").asString()).isEqualTo("TRANSCRIBING");
        assertThat(info.get("medications").get(0).asString()).isEqualTo("lisinopril 10 MG Oral Tablet");
        byte[] audio = mvc.perform(get("/internal/encounters/{id}/recording/audio", id).header("Authorization", transcriber))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsByteArray();
        assertThat(audio).isEqualTo(AUDIO);
        mvc.perform(get("/internal/encounters/{id}/recording/audio", id).header("Authorization", clinician))
                .andExpect(status().isForbidden());

        // The transcript arrives (twice: a redelivered result changes nothing).
        String transcript = json.writeValueAsString(Map.of("transcript", CANARY_TRANSCRIPT, "model", "base.en", "audioSeconds", 91.5));
        for (int i = 0; i < 2; i++) {
            mvc.perform(post("/internal/encounters/{id}/transcript", id).header("Authorization", transcriber)
                            .contentType(MediaType.APPLICATION_JSON).content(transcript))
                    .andExpect(status().isNoContent());
        }
        assertThat(dbStatus(id)).isEqualTo("DRAFTING");
        assertThat(jdbc.sql("select count(*) from recording where encounter_id = :id").param("id", id).query(Long.class).single())
                .isZero();
        ConsumerRecord<String, String> created = awaitRecord("encounter.created", id.toString(), r -> true);
        assertThat(created.value()).doesNotContain(CANARY_TRANSCRIPT);
        assertThat(jdbc.sql("select count(*) from outbox_event where aggregate_id = :id and topic = 'encounter.created'")
                .param("id", id).query(Long.class).single()).isEqualTo(1);

        // From here it is an ordinary encounter: the note worker sees the transcript.
        JsonNode ctx = read(mvc.perform(get("/internal/encounters/{id}/draft-context", id).header("Authorization", token("note-worker")))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString());
        assertThat(ctx.get("transcript").asString()).isEqualTo(CANARY_TRANSCRIPT);
        assertThat(jdbc.sql("select actor || ':' || action from audit_event where encounter_id = :id order by id")
                .param("id", id).query(String.class).list())
                .contains("scribe:RECORDING_UPLOADED", "transcriber:RECORDING_READ", "transcriber:TRANSCRIPT_RECEIVED");
    }

    @Test
    void failedTranscriptionIsVisibleAndRetriesTranscription() throws Exception {
        String scribe = token("scribe");
        String admin = token("admin");
        UUID id = upload(scribe);

        ProducerRecord<String, String> dead = new ProducerRecord<>("chartwise.dead-letter", id.toString(), "{}");
        dead.headers().add(KafkaHeaders.DLT_ORIGINAL_TOPIC, "encounter.recorded".getBytes(StandardCharsets.UTF_8));
        dead.headers().add(KafkaHeaders.DLT_EXCEPTION_FQCN, "transcriber.errors.AudioUnreadable".getBytes(StandardCharsets.UTF_8));
        dead.headers().add(KafkaHeaders.DLT_EXCEPTION_MESSAGE, "the recording could not be decoded".getBytes(StandardCharsets.UTF_8));
        kafkaTemplate.send(dead).get();
        await().atMost(Duration.ofSeconds(20)).untilAsserted(() -> assertThat(dbStatus(id)).isEqualTo("DRAFT_FAILED"));
        assertThat(jdbc.sql("select failure_reason from encounter where id = :id").param("id", id).query(String.class).single())
                .isEqualTo("AudioUnreadable: the recording could not be decoded");

        // Retrying goes back to transcription, not drafting: there is no transcript yet.
        mvc.perform(post("/api/admin/encounters/{id}/retry", id).header("Authorization", admin)).andExpect(status().isOk());
        assertThat(dbStatus(id)).isEqualTo("TRANSCRIBING");
        awaitRecord("encounter.recorded", id.toString(), r -> json.readTree(r.value()).get("attempt").asInt() == 2);
    }

    @Test
    void nonAudioUploadsAreRejected() throws Exception {
        mvc.perform(multipart("/api/encounters/recording").file(new MockMultipartFile("file", "notes.txt", "text/plain", AUDIO))
                        .param("patientId", PATIENT_ID).header("Authorization", token("scribe")))
                .andExpect(status().isBadRequest());
    }

    private UUID upload(String auth) throws Exception {
        String body = mvc.perform(multipart("/api/encounters/recording")
                        .file(new MockMultipartFile("file", "visit.wav", "audio/wav", AUDIO))
                        .param("patientId", PATIENT_ID).header("Authorization", auth))
                .andExpect(status().isAccepted()).andReturn().getResponse().getContentAsString();
        return UUID.fromString(read(body).get("id").asString());
    }

    private String dbStatus(UUID id) {
        return jdbc.sql("select status from encounter where id = :id").param("id", id).query(String.class).single();
    }
}
