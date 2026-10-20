#!/usr/bin/env python3
"""Renders synthetic visit transcripts as two-channel recordings with macOS text-to-speech.

The clinician is on the left channel and the patient's side of the room (patient, family) on the
right, the way a telehealth call or a two-microphone exam room records them. Each line is spoken
by a different system voice, with a short pause between turns. The output is for testing the
transcriber end to end; it is synthetic speech from synthetic visits, never a real recording.

    python3 tools/audio/synthesize.py --visits tools/transcripts/out/visits.json --count 5 --out tools/audio/out

Needs macOS (`say`). Standard library only. Writes <index>.wav plus manifest.json mapping each
file to its visit, so evaluations can compare the transcriber's output with the source text.
"""

import argparse
import json
import subprocess
import tempfile
import wave
from pathlib import Path

RATE = 16_000
VOICES = {"Doctor": ("Samantha", 0), "Patient": ("Reed (English (US))", 1), "Daughter": ("Flo (English (US))", 1)}
GAP_SECONDS = 0.45


def speak(text: str, voice: str, workdir: Path) -> bytes:
    """Mono 16-bit PCM at 16 kHz for one utterance."""
    out = workdir / "line.wav"
    subprocess.run(["say", "-v", voice, "-o", str(out), "--file-format=WAVE", f"--data-format=LEI16@{RATE}", text],
                   check=True)
    with wave.open(str(out)) as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2
        return w.readframes(w.getnframes())


def render(transcript: str, path: Path) -> float:
    """Writes a stereo WAV for the transcript; returns its length in seconds."""
    left, right = bytearray(), bytearray()
    silence = bytes(int(RATE * GAP_SECONDS) * 2)
    with tempfile.TemporaryDirectory() as tmp:
        for line in transcript.splitlines():
            speaker, _, text = line.partition(":")
            if speaker not in VOICES or not text.strip():
                continue  # "[crosstalk]" and the like have no words to say
            voice, channel = VOICES[speaker]
            pcm = speak(text.strip(), voice, Path(tmp))
            mine, other = (left, right) if channel == 0 else (right, left)
            mine += pcm + silence
            other += bytes(len(pcm)) + silence
    frames = bytearray()
    for i in range(0, len(left), 2):
        frames += left[i:i + 2] + right[i:i + 2]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    return len(left) / 2 / RATE


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--visits", default=str(Path(__file__).resolve().parents[1] / "transcripts" / "out" / "visits.json"))
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "out"))
    args = ap.parse_args()

    visits = json.loads(Path(args.visits).read_text())[args.offset: args.offset + args.count]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, visit in enumerate(visits, args.offset):
        path = out / f"{i}.wav"
        seconds = render(visit["transcript"], path)
        manifest.append({"file": path.name, "visit_index": i, "patientId": visit["patientId"], "seconds": round(seconds, 1)})
        print(f"{path.name}: {seconds:.0f} s")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main()
