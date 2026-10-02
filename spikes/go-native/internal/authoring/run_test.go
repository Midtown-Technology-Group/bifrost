package authoring

import (
	"bytes"
	"context"
	"errors"
	"io"
	"strings"
	"testing"
)

func TestTypedHandlerBoundary(t *testing.T) {
	type Input struct {
		Name string `json:"name"`
	}
	type Output struct {
		Greeting string `json:"greeting"`
	}
	setup := func(ctx context.Context, _ io.Reader) (context.Context, func(), error) { return ctx, func() {}, nil }
	for _, test := range []struct {
		name, input string
		args        []string
		valid       bool
	}{
		{"normal", `{"name":"Go"}`, []string{"--local"}, true},
		{"unknown-field", `{"name":"Go","scope":"other-org"}`, []string{"--local"}, false},
		{"trailing-input", `{"name":"Go"} {}`, []string{"--local"}, false},
		{"nonlocal", `{"name":"Go"}`, nil, false},
		{"unexpected-argument", `{"name":"Go"}`, []string{"--local", "execute"}, false},
	} {
		t.Run(test.name, func(t *testing.T) {
			called := false
			var output bytes.Buffer
			err := Run(context.Background(), test.args, strings.NewReader(test.input), &output, strings.NewReader(""), setup, func(_ context.Context, in Input) (Output, error) {
				called = true
				return Output{Greeting: "hello " + in.Name}, nil
			})
			if test.valid {
				if err != nil || !called || output.String() != "{\"greeting\":\"hello Go\"}\n" {
					t.Fatalf("typed invocation failed: %v", err)
				}
			} else if err == nil || called || output.Len() != 0 {
				t.Fatal("invalid input invoked handler")
			}
		})
	}
}

func TestProvisionFailurePreventsHandler(t *testing.T) {
	expected := errors.New("provision unavailable")
	setup := func(context.Context, io.Reader) (context.Context, func(), error) { return nil, nil, expected }
	called := false
	err := Run(context.Background(), []string{"--local"}, strings.NewReader("{}"), io.Discard, strings.NewReader(""), setup, func(context.Context, struct{}) (struct{}, error) { called = true; return struct{}{}, nil })
	if !errors.Is(err, expected) || called {
		t.Fatal("provision failure crossed handler boundary")
	}
}
