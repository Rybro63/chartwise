from asr_metrics import labelled_words, score, word_errors


def test_normalised_words_ignore_case_punctuation_and_number_style():
    assert labelled_words("Doctor: Let's do TWENTY milligrams.\n[crosstalk]\nPatient: OK, 98.6!") == [
        ("Doctor", "let"), ("Doctor", "s"), ("Doctor", "do"), ("Doctor", "20"), ("Doctor", "mg"),
        ("Patient", "ok"), ("Patient", "98.6")]


def test_word_errors_counts_substitutions_insertions_deletions():
    assert word_errors("a b c d".split(), "a x c d e".split()) == 2


def test_perfect_transcript():
    s = score("Doctor: Still on the lisinopril?\nPatient: Yes.", "Doctor: Still on the lisinopril?\nPatient: Yes.")
    assert s["wer"] == 0 and s["speaker_accuracy"] == 1 and s["drugs_heard"] == s["drugs_spoken"] == 1


def test_misheard_drug_and_wrong_speaker():
    s = score("Doctor: Any acetaminophen?\nPatient: Some.", "Doctor: Any a set of minifen?\nDoctor: Some.")
    assert s["missed_drugs"] == ["acetaminophen"]
    assert s["speaker_accuracy"] == 0.5  # "any" and "some" matched; "some" has the wrong speaker
    assert s["wer"] == 1.3333  # 1 substitution + 3 insertions over 3 reference words (WER can exceed 1)


def test_family_member_maps_to_the_patient_channel():
    s = score("Daughter: Evening pills get skipped.", "Patient: Evening pills get skipped.", speakers={"Daughter": "Patient"})
    assert s["speaker_accuracy"] == 1
