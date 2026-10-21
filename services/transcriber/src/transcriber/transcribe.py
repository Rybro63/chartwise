"""Speech to transcript with a local Whisper model (faster-whisper). Audio never leaves the process.

Speaker labels come from the recording, not from a diarisation model: a two-channel recording
(telehealth calls, two-microphone exam rooms) has the clinician on one channel and the patient's
side of the room on the other. Each channel is cut into utterances by voice activity detection,
which gives exact start and end times, and each utterance is transcribed on its own; the
utterances from both channels are then interleaved by start time. (Transcribing a whole channel
and interleaving Whisper's segments or word timestamps put words on the wrong side of the other
speaker's turns, and hotwords leaked into the silences as invented text.) A one-channel recording is transcribed without speaker labels; the
reviewer sees "Speaker:" lines.
"""

import io
import time
from dataclasses import dataclass, field

import av
import numpy as np
from faster_whisper import WhisperModel, decode_audio
from faster_whisper.vad import VadOptions, get_speech_timestamps

SAMPLE_RATE = 16_000


# Drugs a visit may name that aren't in the patient's record: over-the-counter ones, and the most
# commonly prescribed outpatient generics (new prescriptions, a relative's medicine). Without a
# hint Whisper heard "acetaminophen" as "a set of minifen" and "tamsulosin" as "damn sullison".
# The prompt holds about 224 tokens, so the patient's own medications go first.
COMMON_DRUGS = [
    "acetaminophen", "ibuprofen", "naproxen", "aspirin", "loratadine", "cetirizine", "diphenhydramine",
    "atorvastatin", "lisinopril", "levothyroxine", "metformin", "amlodipine", "metoprolol", "omeprazole",
    "simvastatin", "losartan", "albuterol", "gabapentin", "hydrochlorothiazide", "sertraline", "rosuvastatin",
    "tamsulosin", "escitalopram", "bupropion", "furosemide", "pantoprazole", "trazodone", "montelukast",
    "clopidogrel", "prednisone", "amoxicillin", "warfarin", "apixaban", "insulin",
]


@dataclass(frozen=True)
class Segment:
    """One utterance: what one speaker said between pauses."""
    start: float
    end: float
    speaker: str
    text: str


@dataclass
class Transcript:
    segments: list[Segment]
    audio_seconds: float
    channels: int
    model: str
    processing_seconds: float = 0.0
    speakers: tuple[str, ...] = field(default_factory=tuple)

    def text(self) -> str:
        """One line per turn, "Speaker: words", merging consecutive segments from one speaker."""
        lines: list[tuple[str, str]] = []
        for seg in self.segments:
            if lines and lines[-1][0] == seg.speaker:
                lines[-1] = (seg.speaker, f"{lines[-1][1]} {seg.text}")
            else:
                lines.append((seg.speaker, seg.text))
        return "\n".join(f"{speaker}: {' '.join(text.split())}" for speaker, text in lines)


def channel_count(audio: bytes) -> int:
    with av.open(io.BytesIO(audio)) as container:
        return container.streams.audio[0].codec_context.channels


class Transcriber:
    def __init__(self, model_name: str = "base.en", speakers: tuple[str, str] = ("Doctor", "Patient"),
                 device: str = "cpu", compute_type: str = "int8", download_root: str | None = None):
        self.model_name = model_name
        self.speakers = speakers
        self._model = WhisperModel(model_name, device=device, compute_type=compute_type, download_root=download_root)

    def transcribe(self, audio: bytes, hotwords: list[str] | None = None) -> Transcript:
        """audio: any container ffmpeg reads (wav, mp3, m4a, ogg...). hotwords are words the visit
        is likely to contain, such as the patient's medication names; they go into Whisper's prompt."""
        started = time.monotonic()
        channels = channel_count(audio)
        if channels >= 2:
            left, right = decode_audio(io.BytesIO(audio), sampling_rate=SAMPLE_RATE, split_stereo=True)
            tracks = [(self.speakers[0], left), (self.speakers[1], right)]
        else:
            tracks = [("Speaker", decode_audio(io.BytesIO(audio), sampling_rate=SAMPLE_RATE))]

        words: list[Segment] = []
        for speaker, samples in tracks:
            words += self._utterances(samples, speaker, [*(hotwords or []), *COMMON_DRUGS])
        words.sort(key=lambda w: (w.start, w.end))
        seconds = max(len(samples) for _, samples in tracks) / SAMPLE_RATE
        return Transcript(words, round(seconds, 1), channels, self.model_name,
                          round(time.monotonic() - started, 1), tuple(s for s, _ in tracks))

    def _utterances(self, samples: np.ndarray, speaker: str, hotwords: list[str]) -> list[Segment]:
        if not np.any(samples):
            return []
        # Utterances are capped at 20 s: Whisper decodes 30 s windows and shares its context between
        # the prompt and the output, so a long run of speech (a clinician's whole exam and plan)
        # lost most of its words once the drug-name prompt grew.
        chunks = get_speech_timestamps(samples, VadOptions(min_silence_duration_ms=600, speech_pad_ms=150,
                                                           max_speech_duration_s=20))
        # A punctuated sentence naming the likely drugs: Whisper copies a prompt's style, so a bare
        # word list (faster-whisper's hotwords) cost the transcript its casing and punctuation.
        prompt = f"A clinic visit. Medications discussed may include {', '.join(dict.fromkeys(hotwords))}." if hotwords else None
        out = []
        for c in chunks:
            found, _ = self._model.transcribe(
                samples[c["start"]:c["end"]], language="en", beam_size=5, vad_filter=False,
                without_timestamps=True, condition_on_previous_text=False, initial_prompt=prompt)
            text = " ".join(s.text.strip() for s in found).strip()
            if text:
                out.append(Segment(round(c["start"] / SAMPLE_RATE, 2), round(c["end"] / SAMPLE_RATE, 2), speaker, text))
        return out
