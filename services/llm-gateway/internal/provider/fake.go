package provider

import (
	"context"
	"encoding/json"
	"fmt"
	"math/rand/v2"
	"regexp"
	"strings"
	"time"
)

// Fake is a deterministic stand-in for the LLM, used for local development, load tests and chaos
// experiments where calling a real model would be slow, costly and non-reproducible.
//
// It understands the note worker's prompt contract (soap-v1): it reads the <transcript>,
// <active_conditions> and <active_medications> blocks and returns JSON in the worker's schema.
// With HallucinationRate > 0 it inserts a medication that appears in neither the record nor the
// transcript, which is exactly what the worker's safety check must catch.
//
// It also answers the note eval's judge prompt (judge-v1, a <note_sentences> block) with a crude
// word-overlap verdict, so the judge's plumbing can be exercised for free. Its labels say nothing
// about note quality.
type Fake struct {
	Latency           time.Duration
	Jitter            time.Duration
	HallucinationRate float64
}

func (f *Fake) Name() string { return "fake" }

var hallucinationPool = []string{"warfarin", "digoxin", "methotrexate", "amiodarone", "clozapine", "tramadol"}

func (f *Fake) Complete(ctx context.Context, req Request) (Response, error) {
	delay := f.Latency
	if f.Jitter > 0 {
		delay += time.Duration(rand.Int64N(int64(f.Jitter)))
	}
	select {
	case <-time.After(delay):
	case <-ctx.Done():
		return Response{}, &Error{Retryable: true, Status: 504, Reason: "provider call timed out"}
	}

	if strings.Contains(req.User, "<note_sentences>") {
		return f.judge(req)
	}

	transcript := block(req.User, "transcript")
	conditions := lines(block(req.User, "active_conditions"))
	meds := lines(block(req.User, "active_medications"))

	var patientSaid, clinicianSaid []string
	for _, line := range lines(transcript) {
		lower := strings.ToLower(line)
		switch {
		case strings.HasPrefix(lower, "patient:"):
			patientSaid = append(patientSaid, strings.TrimSpace(line[len("patient:"):]))
		case strings.HasPrefix(lower, "doctor:"), strings.HasPrefix(lower, "clinician:"):
			clinicianSaid = append(clinicianSaid, strings.TrimSpace(line[strings.Index(line, ":")+1:]))
		}
	}

	type med struct {
		Name   string `json:"name"`
		Status string `json:"status"`
	}
	var mentioned []med
	var plan []string
	for _, m := range meds {
		name := firstWord(m)
		if name == "" {
			continue
		}
		if strings.Contains(strings.ToLower(transcript), strings.ToLower(name)) {
			mentioned = append(mentioned, med{Name: name, Status: "continued"})
			plan = append(plan, fmt.Sprintf("Continue %s as prescribed.", m))
		}
	}
	if f.HallucinationRate > 0 && rand.Float64() < f.HallucinationRate {
		drug := hallucinationPool[rand.IntN(len(hallucinationPool))]
		plan = append(plan, fmt.Sprintf("Start %s 5 mg daily.", drug))
		mentioned = append(mentioned, med{Name: drug, Status: "started"})
	}
	plan = append(plan, "Follow up in 4 weeks or sooner if symptoms worsen.")

	assessment := "Visit reviewed."
	if len(conditions) > 0 {
		assessment = "Known conditions: " + strings.Join(conditions, "; ") + "."
	}
	objective := "No vitals documented in transcript."
	if len(clinicianSaid) > 0 {
		objective = "Clinician observations: " + strings.Join(clinicianSaid, " ")
	}
	subjective := "Patient did not report symptoms."
	if len(patientSaid) > 0 {
		subjective = "Patient reports: " + strings.Join(patientSaid, " ")
	}

	out, _ := json.Marshal(map[string]any{
		"subjective":            subjective,
		"objective":             objective,
		"assessment":            assessment,
		"plan":                  strings.Join(plan, " "),
		"medications_mentioned": nonNil(mentioned),
	})
	return Response{
		Text:         string(out),
		Model:        "fake-soap-v1",
		StopReason:   "end_turn",
		InputTokens:  int64(len(req.System)+len(req.User)) / 4,
		OutputTokens: int64(len(out)) / 4,
	}, nil
}

func block(s, tag string) string {
	re := regexp.MustCompile(`(?s)<` + tag + `>(.*?)</` + tag + `>`)
	m := re.FindStringSubmatch(s)
	if m == nil {
		return ""
	}
	return strings.TrimSpace(m[1])
}

func lines(s string) []string {
	var out []string
	for _, l := range strings.Split(s, "\n") {
		l = strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(l), "- "))
		if l != "" && l != "(none recorded)" {
			out = append(out, l)
		}
	}
	return out
}

func firstWord(s string) string {
	fields := strings.Fields(s)
	if len(fields) == 0 {
		return ""
	}
	return strings.ToLower(strings.Trim(fields[0], ",.;:"))
}

func nonNil[T any](s []T) []T {
	if s == nil {
		return []T{}
	}
	return s
}

var (
	numberedLine = regexp.MustCompile(`^\[(\d+)\]\s*(.*)$`)
	contentWord  = regexp.MustCompile(`[a-z0-9][a-z0-9./]{3,}`)
)

// judge labels each numbered note sentence "supported" when at least half of its content words
// appear in the transcript or record, citing the transcript lines that share the most words.
func (f *Fake) judge(req Request) (Response, error) {
	source := strings.ToLower(block(req.User, "transcript") + "\n" + block(req.User, "record"))
	known := map[string]bool{}
	for _, w := range contentWord.FindAllString(source, -1) {
		known[strings.Trim(w, "./")] = true
	}
	transcriptLines := map[int]map[string]bool{}
	for _, l := range strings.Split(block(req.User, "transcript"), "\n") {
		if m := numberedLine.FindStringSubmatch(strings.TrimSpace(l)); m != nil {
			n := atoi(m[1])
			transcriptLines[n] = map[string]bool{}
			for _, w := range contentWord.FindAllString(strings.ToLower(m[2]), -1) {
				transcriptLines[n][strings.Trim(w, "./")] = true
			}
		}
	}

	type verdict struct {
		N      int    `json:"n"`
		Label  string `json:"label"`
		Lines  []int  `json:"lines"`
		Reason string `json:"reason"`
	}
	verdicts := []verdict{}
	for _, l := range strings.Split(block(req.User, "note_sentences"), "\n") {
		m := numberedLine.FindStringSubmatch(strings.TrimSpace(l))
		if m == nil {
			continue
		}
		words := contentWord.FindAllString(strings.ToLower(m[2]), -1)
		hits, best, bestLine := 0, 0, 0
		for _, w := range words {
			if known[strings.Trim(w, "./")] {
				hits++
			}
		}
		for n, lw := range transcriptLines {
			shared := 0
			for _, w := range words {
				if lw[strings.Trim(w, "./")] {
					shared++
				}
			}
			if shared > best || (shared == best && shared > 0 && n < bestLine) {
				best, bestLine = shared, n
			}
		}
		v := verdict{N: atoi(m[1]), Label: "unsupported", Lines: []int{}, Reason: "fake judge: little word overlap with the sources"}
		if len(words) == 0 || hits*2 >= len(words) {
			v.Label, v.Reason = "supported", "fake judge: words found in the sources"
		}
		if bestLine > 0 {
			v.Lines = []int{bestLine}
		}
		verdicts = append(verdicts, v)
	}
	out, _ := json.Marshal(map[string]any{"sentences": verdicts, "omissions": []any{}})
	return Response{
		Text:         string(out),
		Model:        "fake-judge-v1",
		StopReason:   "end_turn",
		InputTokens:  int64(len(req.System)+len(req.User)) / 4,
		OutputTokens: int64(len(out)) / 4,
	}, nil
}

func atoi(s string) int {
	n := 0
	for _, c := range s {
		n = n*10 + int(c-'0')
	}
	return n
}
