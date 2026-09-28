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
