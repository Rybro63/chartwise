package provider

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/anthropics/anthropic-sdk-go"
	"github.com/anthropics/anthropic-sdk-go/option"
)

// Anthropic calls the Claude Messages API.
type Anthropic struct {
	client anthropic.Client
	model  string
	effort string
}

func NewAnthropic(apiKey, workspaceID, model, effort string) *Anthropic {
	opts := []option.RequestOption{
		// The gateway owns retries (with its own backoff, budget and breaker accounting), so the
		// SDK's built-in retries are off to avoid multiplying attempts.
		option.WithMaxRetries(0),
	}
	if apiKey != "" {
		opts = append(opts, option.WithAPIKey(apiKey))
	}
	// Keys that aren't scoped to a workspace must name one on every request.
	if workspaceID != "" {
		opts = append(opts, option.WithHeader("anthropic-workspace-id", workspaceID))
	}
	return &Anthropic{client: anthropic.NewClient(opts...), model: model, effort: effort}
}

func (a *Anthropic) Name() string { return "anthropic" }

func (a *Anthropic) Complete(ctx context.Context, req Request) (Response, error) {
	maxTokens := int64(req.MaxTokens)
	if maxTokens <= 0 {
		maxTokens = 16000
	}
	params := anthropic.MessageNewParams{
		Model:     anthropic.Model(a.model),
		MaxTokens: maxTokens,
		Messages: []anthropic.MessageParam{
			anthropic.NewUserMessage(anthropic.NewTextBlock(req.User)),
		},
	}
	if req.System != "" {
		params.System = []anthropic.TextBlockParam{{Text: req.System}}
	}
	if req.JSONSchema != "" {
		var schema map[string]any
		if err := json.Unmarshal([]byte(req.JSONSchema), &schema); err != nil {
			return Response{}, &Error{Retryable: false, Status: 400, Reason: "json_schema is not valid JSON"}
		}
		params.OutputConfig.Format = anthropic.JSONOutputFormatParam{Schema: schema}
	}
	if a.effort != "" {
		params.OutputConfig.Effort = anthropic.OutputConfigEffort(a.effort)
	}

	msg, err := a.client.Messages.New(ctx, params,
		// If a safety classifier declines the request, re-serve it on Anthropic's recommended
		// fallback model inside the same call instead of failing the draft.
		option.WithHeaderAdd("anthropic-beta", "server-side-fallback-2026-07-01"),
		option.WithJSONSet("fallbacks", "default"),
	)
	if err != nil {
		return Response{}, classify(ctx, err)
	}

	switch msg.StopReason {
	case anthropic.StopReasonRefusal:
		return Response{}, &Error{Retryable: false, Status: 200, Reason: "model declined the request (refusal)"}
	case anthropic.StopReasonMaxTokens:
		return Response{}, &Error{Retryable: false, Status: 200, Reason: "response truncated at max_tokens"}
	}

	var text strings.Builder
	for _, block := range msg.Content {
		if tb, ok := block.AsAny().(anthropic.TextBlock); ok {
			text.WriteString(tb.Text)
		}
	}
	return Response{
		Text:         text.String(),
		Model:        string(msg.Model),
		StopReason:   string(msg.StopReason),
		InputTokens:  msg.Usage.InputTokens,
		OutputTokens: msg.Usage.OutputTokens,
	}, nil
}

func classify(ctx context.Context, err error) error {
	var apierr *anthropic.Error
	if errors.As(err, &apierr) {
		status := apierr.StatusCode
		pe := &Error{
			Status: status,
			// 408 timeout, 409 conflict, 429 rate limit, 5xx (incl. 529 overloaded) are transient.
			Retryable: status == 408 || status == 409 || status == 429 || status >= 500,
			Reason:    fmt.Sprintf("anthropic API returned %d", status),
		}
		if apierr.Response != nil {
			if secs, convErr := strconv.Atoi(apierr.Response.Header.Get("retry-after")); convErr == nil {
				pe.RetryAfter = time.Duration(secs) * time.Second
			}
		}
		return pe
	}
	if ctx.Err() != nil {
		return &Error{Retryable: true, Status: 504, Reason: "provider call timed out"}
	}
	return &Error{Retryable: true, Status: 503, Reason: "provider unreachable: " + fmt.Sprintf("%T", errors.Unwrap(err))}
}
