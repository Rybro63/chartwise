from pathlib import Path

import pytest

from transcriber.transcribe import Segment, Transcript, channel_count

FIXTURES = Path(__file__).parent / "fixtures"


def test_turns_merge_consecutive_utterances_from_one_speaker():
    t = Transcript([Segment(0.0, 1.0, "Doctor", "How are you?"), Segment(1.5, 2.0, "Doctor", "Any pain?"),
                    Segment(2.4, 3.0, "Patient", "No pain."), Segment(3.5, 4.0, "Doctor", "Good.")],
                   audio_seconds=4.0, channels=2, model="test")
    assert t.text() == "Doctor: How are you? Any pain?\nPatient: No pain.\nDoctor: Good."


def test_channel_count():
    assert channel_count((FIXTURES / "two-speakers.wav").read_bytes()) == 2
    assert channel_count((FIXTURES / "one-channel.wav").read_bytes()) == 1


@pytest.fixture(scope="module")
def transcriber():
    from transcriber.transcribe import Transcriber
    return Transcriber("base.en")


@pytest.mark.model
def test_two_channels_become_labelled_turns_in_order(transcriber):
    # Synthetic speech (tools/audio/synthesize.py): doctor on the left, patient on the right.
    t = transcriber.transcribe((FIXTURES / "two-speakers.wav").read_bytes(), hotwords=["lisinopril"])
    lines = t.text().splitlines()
    assert [line.split(":")[0] for line in lines] == ["Doctor", "Patient", "Doctor"]
    assert "lisinopril" in lines[0].lower()
    assert "every morning" in lines[1].lower()
    assert "four weeks" in lines[2].lower() or "4 weeks" in lines[2].lower()
    assert t.channels == 2 and t.audio_seconds > 5


@pytest.mark.model
def test_one_channel_is_transcribed_without_speaker_labels(transcriber):
    t = transcriber.transcribe((FIXTURES / "one-channel.wav").read_bytes())
    assert t.text().startswith("Speaker: ")
    assert "every morning" in t.text().lower()
