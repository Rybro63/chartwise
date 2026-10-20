-- Sources for the AI draft (prompt soap-v2): which transcript lines each statement cites, and the
-- model's medication list. Encrypted like the note itself; null for older drafts and human edits.
alter table note_version add column draft_detail_ciphertext bytea;
