package com.chartwise.encounter.encounter;

import com.chartwise.encounter.crypto.EncryptedStringConverter;
import jakarta.persistence.Column;
import jakarta.persistence.Convert;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

/**
 * One immutable version of a note. Version 1 is always the unedited AI draft; every clinician or
 * scribe save adds a new row, so "what the AI wrote vs. what the human signed" is always answerable.
 */
@Entity
@Table(name = "note_version")
public class NoteVersion {

    @Id
    private UUID id;

    @Column(name = "encounter_id", nullable = false)
    private UUID encounterId;

    @Column(nullable = false)
    private int version;

    @Enumerated(EnumType.STRING)
    @Column(name = "author_type", nullable = false)
    private AuthorType authorType;

    @Column(nullable = false)
    private String author;

    /** SOAP note as JSON. */
    @Convert(converter = EncryptedStringConverter.class)
    @Column(name = "content_ciphertext", nullable = false)
    private String contentJson;

    /** Safety flags as a JSON array; empty for human versions. */
    @Convert(converter = EncryptedStringConverter.class)
    @Column(name = "safety_flags_ciphertext", nullable = false)
    private String safetyFlagsJson;

    @Column(name = "safety_flag_count", nullable = false)
    private int safetyFlagCount;

    @Column
    private String model;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected NoteVersion() {}

    public NoteVersion(UUID encounterId, int version, AuthorType authorType, String author,
                       String contentJson, String safetyFlagsJson, int safetyFlagCount, String model) {
        this.id = UUID.randomUUID();
        this.encounterId = encounterId;
        this.version = version;
        this.authorType = authorType;
        this.author = author;
        this.contentJson = contentJson;
        this.safetyFlagsJson = safetyFlagsJson;
        this.safetyFlagCount = safetyFlagCount;
        this.model = model;
        this.createdAt = Instant.now();
    }

    public UUID getId() { return id; }
    public UUID getEncounterId() { return encounterId; }
    public int getVersion() { return version; }
    public AuthorType getAuthorType() { return authorType; }
    public String getAuthor() { return author; }
    public String getContentJson() { return contentJson; }
    public String getSafetyFlagsJson() { return safetyFlagsJson; }
    public int getSafetyFlagCount() { return safetyFlagCount; }
    public String getModel() { return model; }
    public Instant getCreatedAt() { return createdAt; }
}
