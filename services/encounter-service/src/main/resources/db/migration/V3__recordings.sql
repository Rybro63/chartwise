-- Visits uploaded as audio: the encounter waits in TRANSCRIBING (no transcript yet) while the
-- transcriber works, and the recording is kept, encrypted, only until its transcript arrives.
alter table encounter alter column transcript_ciphertext drop not null;
alter table encounter drop constraint encounter_status_check;
alter table encounter add constraint encounter_status_check
    check (status in ('TRANSCRIBING', 'DRAFTING', 'IN_REVIEW', 'APPROVED', 'FILED', 'DRAFT_FAILED'));
alter table encounter add column transcribed_at timestamptz;

create table recording (
    encounter_id     uuid        primary key references encounter (id) on delete cascade,
    content_type     text        not null,
    size_bytes       int         not null,
    audio_ciphertext bytea       not null,
    created_at       timestamptz not null default now()
);
