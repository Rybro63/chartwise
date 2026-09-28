from prometheus_client import Counter, Gauge, Histogram

NOTES_DRAFTED = Counter("worker_notes_drafted_total", "Drafts published to note.drafted")
DRAFTS_FLAGGED = Counter("worker_drafts_flagged_total", "Drafts with at least one high-severity safety flag")
SAFETY_FLAGS = Counter("worker_safety_flags_total", "Safety flags raised", ["code"])
SKIPPED = Counter("worker_messages_skipped_total", "Messages skipped without drafting", ["reason"])
DEAD_LETTERS = Counter("worker_dead_letters_total", "Messages sent to the dead-letter topic", ["reason"])
GATEWAY_RETRIES = Counter("worker_gateway_retries_total", "Retries of gateway calls", ["code"])
DRAFT_SECONDS = Histogram(
    "worker_draft_duration_seconds", "Time to draft one note (context + LLM + safety check)",
    buckets=(0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600),
)
BATCH_SIZE = Histogram("worker_batch_size", "Messages per consumed batch", buckets=(1, 2, 5, 10, 20, 50))
IN_FLIGHT = Gauge("worker_inflight_drafts", "Drafts currently being generated")
