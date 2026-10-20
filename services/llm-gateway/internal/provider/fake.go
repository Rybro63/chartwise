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
// It understands the note worker's prompt contract (soap-v2): it reads the numbered <transcript>,
// <active_conditions> and <active_medications> blocks and returns JSON in the worker's schema,
// each statement citing the transcript line it came from. With HallucinationRate > 0 it inserts a
// medication that appears in neither the record nor the transcript and cites no source, which is
// exactly what the worker's safety check must catch.
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

	type statement struct {
		Text            string `json:"text"`
		TranscriptLines []int  `json:"transcript_lines"`
		FromRecord      bool   `json:"from_record"`
	}
	type med struct {
		Name   string `json:"name"`
		Status string `json:"status"`
		Dose   string `json:"dose"`
	}
	var subjective, objective, assessment, plan []statement
	var mentioned []med

	// Transcript lines arrive numbered ("[3] Patient: ..."); every statement cites its line.
	type utterance struct {
		n    int
		text string
	}
	var said []utterance
	for i, line := range strings.Split(transcript, "\n") {
		n, text := i+1, strings.TrimSpace(line)
		if m := numberedLine.FindStringSubmatch(text); m != nil {
			n, text = atoi(m[1]), m[2]
		}
		said = append(said, utterance{n, text})
		lower := strings.ToLower(text)
		switch {
		case strings.HasPrefix(lower, "patient:"):
			subjective = append(subjective, statement{"Patient reports: " + strings.TrimSpace(text[len("patient:"):]), []int{n}, false})
		case strings.HasPrefix(lower, "doctor:"), strings.HasPrefix(lower, "clinician:"):
			objective = append(objective, statement{"Clinician: " + strings.TrimSpace(text[strings.Index(text, ":")+1:]), []int{n}, false})
		}
	}
	linesMentioning := func(word string) []int {
		var out []int
		for _, u := range said {
			if strings.Contains(strings.ToLower(u.text), word) {
				out = append(out, u.n)
			}
		}
		return out
	}

	for _, m := range meds {
		name := firstWord(m)
		if name == "" {
			continue
		}
		if cited := linesMentioning(name); len(cited) > 0 {
			mentioned = append(mentioned, med{Name: name, Status: "continued"})
			plan = append(plan, statement{fmt.Sprintf("Continue %s as prescribed.", m), cited, true})
		}
	}
	if f.HallucinationRate > 0 && rand.Float64() < f.HallucinationRate {
		// Invented, so it cites nothing: both the medication and the sourcing checks should fire.
		drug := hallucinationPool[rand.IntN(len(hallucinationPool))]
		plan = append(plan, statement{fmt.Sprintf("Start %s 5 mg daily.", drug), []int{}, false})
		mentioned = append(mentioned, med{Name: drug, Status: "started", Dose: "5 mg"})
	}
	for _, word := range []string{"back in", "follow up", "follow-up"} {
		if cited := linesMentioning(word); len(cited) > 0 {
			plan = append(plan, statement{"Follow up as discussed.", cited[len(cited)-1:], false})
			break
		}
	}

	if len(conditions) > 0 {
		assessment = append(assessment, statement{"Known conditions: " + strings.Join(conditions, "; ") + ".", []int{}, true})
	} else {
		assessment = append(assessment, statement{"No active conditions recorded.", []int{}, true})
	}
	if len(subjective) == 0 {
		subjective = append(subjective, statement{"Patient symptoms not documented.", []int{}, false})
	}
	if len(objective) == 0 {
		objective = append(objective, statement{"No vitals documented in transcript.", []int{}, false})
	}

	out, _ := json.Marshal(map[string]any{
		"subjective":            subjective,
		"objective":             objective,
		"assessment":            assessment,
		"plan":                  nonNil(plan),
		"medications_mentioned": nonNil(mentioned),
	})
	return Response{
		Text:         string(out),
		Model:        "fake-soap-v2",
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
