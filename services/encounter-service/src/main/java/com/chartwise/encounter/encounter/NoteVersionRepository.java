package com.chartwise.encounter.encounter;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface NoteVersionRepository extends JpaRepository<NoteVersion, UUID> {

    List<NoteVersion> findByEncounterIdOrderByVersionAsc(UUID encounterId);

    Optional<NoteVersion> findByEncounterIdAndVersion(UUID encounterId, int version);

    long countByEncounterIdAndAuthorType(UUID encounterId, AuthorType authorType);
}
