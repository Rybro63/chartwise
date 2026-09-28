-- Chartwise encounter service schema.
-- Columns suffixed _ciphertext hold AES-GCM encrypted PHI (see FieldEncryptor); everything
-- else is safe to read without the key.

create table app_user (
    id            uuid primary key,
    username      text        not null unique,
    password_hash text        not null,
    role          text        not null check (role in ('CLINICIAN', 'SCRIBE', 'ADMIN', 'SERVICE')),
    display_name  text        not null,
    created_at    timestamptz not null default now()
);

create table encounter (
    id                        uuid primary key,
    patient_id                text        not null,
    patient_name_ciphertext   bytea       not null,
    transcript_ciphertext     bytea       not null,
    status                    text        not null check (status in ('DRAFTING', 'IN_REVIEW', 'APPROVED', 'FILED', 'DRAFT_FAILED')),
    created_by                text        not null,
    current_version           int         not null default 0,
    draft_attempt             int         not null default 1,
    safety_flag_count         int         not null default 0,
    approved_version          int,
    approved_by               text,
    approved_at               timestamptz,
    fhir_document_id          text,
    failure_reason            text,
    created_at                timestamptz not null,
    drafted_at                timestamptz,
    updated_at                timestamptz not null,
    lock_version              bigint      not null default 0
);
create index encounter_status_created on encounter (status, created_at);

create table note_version (
    id                      uuid primary key,
    encounter_id            uuid        not null references encounter (id) on delete cascade,
    version                 int         not null,
    author_type             text        not null check (author_type in ('AI', 'HUMAN')),
    author                  text        not null,
    content_ciphertext      bytea       not null,
    safety_flags_ciphertext bytea       not null,
    safety_flag_count       int         not null default 0,
    model                   text,
    created_at              timestamptz not null,
    unique (encounter_id, version)
);

-- Transactional outbox: rows are written in the same transaction as the state change they
-- describe and published to Kafka by OutboxRelay. Payloads carry IDs only, never PHI.
create table outbox_event (
    id           uuid primary key,
    aggregate_id uuid        not null,
    topic        text        not null,
    message_key  text        not null,
    payload      jsonb       not null,
    traceparent  text,
    created_at   timestamptz not null default now(),
    published_at timestamptz,
    attempts     int         not null default 0,
    last_error   text
);
create index outbox_unpublished on outbox_event (created_at) where published_at is null;

-- Idempotent consumers record (consumer, message key) in the same transaction as their effect.
create table processed_message (
    consumer     text        not null,
    message_key  text        not null,
    processed_at timestamptz not null default now(),
    primary key (consumer, message_key)
);

-- Append-only audit trail. Contains IDs and actions only, never PHI, so it survives retention.
create table audit_event (
    id           bigserial primary key,
    occurred_at  timestamptz not null default now(),
    actor        text        not null,
    actor_role   text        not null,
    action       text        not null,
    encounter_id uuid,
    note_version int,
    detail       jsonb       not null default '{}'
);
create index audit_event_encounter on audit_event (encounter_id, occurred_at);
create index audit_event_time on audit_event (occurred_at);

create function audit_event_is_append_only() returns trigger
    language plpgsql as
$$
begin
    raise exception 'audit_event is append-only (% rejected)', tg_op;
end
$$;

create trigger audit_event_no_update_delete
    before update or delete on audit_event
    for each row execute function audit_event_is_append_only();

create trigger audit_event_no_truncate
    before truncate on audit_event
    for each statement execute function audit_event_is_append_only();
