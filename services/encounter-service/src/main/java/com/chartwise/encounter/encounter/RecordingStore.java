package com.chartwise.encounter.encounter;

import com.chartwise.encounter.crypto.FieldEncryptor;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/**
 * Uploaded visit recordings, encrypted like every other PHI column. A recording exists only
 * between upload and transcription: storing the transcript deletes it.
 */
@Component
public class RecordingStore {

    public record Recording(String contentType, int sizeBytes, byte[] audio) {}

    private final JdbcClient jdbc;
    private final FieldEncryptor encryptor;

    public RecordingStore(JdbcClient jdbc, FieldEncryptor encryptor) {
        this.jdbc = jdbc;
        this.encryptor = encryptor;
    }

    public void save(UUID encounterId, String contentType, byte[] audio) {
        jdbc.sql("insert into recording (encounter_id, content_type, size_bytes, audio_ciphertext) values (:id, :type, :size, :audio)")
                .param("id", encounterId).param("type", contentType).param("size", audio.length)
                .param("audio", encryptor.encryptBytes(audio))
                .update();
    }

    public Optional<Recording> find(UUID encounterId) {
        return jdbc.sql("select content_type, size_bytes, audio_ciphertext from recording where encounter_id = :id")
                .param("id", encounterId)
                .query((rs, i) -> new Recording(rs.getString(1), rs.getInt(2), encryptor.decryptBytes(rs.getBytes(3))))
                .optional();
    }

    public boolean exists(UUID encounterId) {
        return jdbc.sql("select count(*) from recording where encounter_id = :id").param("id", encounterId)
                .query(Long.class).single() > 0;
    }

    public void delete(UUID encounterId) {
        jdbc.sql("delete from recording where encounter_id = :id").param("id", encounterId).update();
    }
}
