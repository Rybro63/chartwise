package com.chartwise.encounter.encounter;

import jakarta.persistence.LockModeType;
import java.time.Instant;
import java.util.Collection;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;

public interface EncounterRepository extends JpaRepository<Encounter, UUID> {

    List<Encounter> findByStatusOrderByCreatedAtAsc(EncounterStatus status, Pageable page);

    List<Encounter> findAllByOrderByCreatedAtDesc(Pageable page);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select e from Encounter e where e.id = :id")
    Optional<Encounter> findForUpdate(UUID id);

    @Query("select e.id from Encounter e where e.status in :statuses and e.updatedAt < :cutoff")
    List<UUID> findPurgeable(Collection<EncounterStatus> statuses, Instant cutoff, Pageable page);

    long countByStatus(EncounterStatus status);
}
