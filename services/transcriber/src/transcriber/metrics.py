from prometheus_client import Counter, Histogram

TRANSCRIBED = Counter("transcriber_recordings_total", "Recordings transcribed")
AUDIO_SECONDS = Counter("transcriber_audio_seconds_total", "Seconds of audio transcribed")
SECONDS = Histogram("transcriber_duration_seconds", "Time to transcribe one recording",
                    buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1200))
SKIPPED = Counter("transcriber_messages_skipped_total", "Messages skipped (already transcribed or failed)", ["reason"])
DEAD_LETTERS = Counter("transcriber_dead_letters_total", "Messages sent to the dead-letter topic", ["reason"])
