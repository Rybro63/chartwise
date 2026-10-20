package provider

import (
	"context"
	"encoding/json"
	"testing"
)

func TestFakeJudgeLabelsEachNumberedSentence(t *testing.T) {
	user := "<record>\nActive medications: lisinopril 10 MG Oral Tablet\n</record>\n<transcript>\n" +
		"[1] Doctor: How is the blood pressure?\n[2] Patient: Fine, I take my lisinopril every morning.\n</transcript>\n" +
		"<note_sentences>\n[1] (subjective) Takes lisinopril every morning.\n[2] (plan) Start warfarin tablets nightly.\n</note_sentences>"
	resp, err := (&Fake{}).Complete(context.Background(), Request{System: "judge", User: user})
	if err != nil {
		t.Fatal(err)
	}
	var out struct {
		Sentences []struct {
			N     int    `json:"n"`
			Label string `json:"label"`
			Lines []int  `json:"lines"`
		} `json:"sentences"`
	}
	if err := json.Unmarshal([]byte(resp.Text), &out); err != nil {
		t.Fatalf("judge output is not JSON: %v", err)
	}
	if resp.Model != "fake-judge-v1" || len(out.Sentences) != 2 {
		t.Fatalf("model %q, %d verdicts", resp.Model, len(out.Sentences))
	}
	if out.Sentences[0].Label != "supported" || len(out.Sentences[0].Lines) != 1 || out.Sentences[0].Lines[0] != 2 {
		t.Fatalf("sentence 1 = %+v, want supported citing line 2", out.Sentences[0])
	}
	if out.Sentences[1].Label != "unsupported" {
		t.Fatalf("sentence 2 = %+v, want unsupported", out.Sentences[1])
	}
}
