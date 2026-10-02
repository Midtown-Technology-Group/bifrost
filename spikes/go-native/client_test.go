package bifrost

import (
	"context"
	"crypto/x509"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func testClient(t *testing.T, s *httptest.Server) *Client {
	t.Helper()
	roots := x509.NewCertPool()
	roots.AddCert(s.Certificate())
	c, err := NewClient(Provision{Endpoint: s.URL, Bearer: "synthetic-bounded-fixture", OrganizationID: "org-a", SolutionID: "install-a"}, roots)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(c.Close)
	return c
}
func TestGetContract(t *testing.T) {
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" || r.URL.Path != "/api/sdk/integrations/get" || r.Header.Get("Authorization") != "Bearer synthetic-bounded-fixture" {
			t.Error("request identity mismatch")
		}
		var body map[string]any
		if json.NewDecoder(r.Body).Decode(&body) != nil || body["name"] != "Fixture" || body["scope"] != "org-a" || body["solution"] != "install-a" || len(body) != 3 {
			t.Error("request projection mismatch")
		}
		_, _ = w.Write([]byte(`{"integration_id":"synthetic","entity_id":null,"entity_name":null,"config":{"credential":"secret-value","counter":9007199254740993},"oauth":null,"config_secret_keys":["credential"]}`))
	}))
	defer s.Close()
	c := testClient(t, s)
	i, err := c.Integrations.Get(context.Background(), "Fixture", nil)
	if err != nil || i == nil || string(i.Config["counter"]) != "9007199254740993" {
		t.Fatal("response or numeric precision mismatch")
	}
	if !i.HasConfigValue("credential") {
		t.Fatal("configuration missing")
	}
}
func TestRedirectsNeverDeliver(t *testing.T) {
	var calls atomic.Int32
	target := httptest.NewTLSServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { calls.Add(1) }))
	defer target.Close()
	for _, code := range []int{301, 302, 303, 307, 308} {
		t.Run(http.StatusText(code), func(t *testing.T) {
			s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, target.URL, code) }))
			defer s.Close()
			c := testClient(t, s)
			_, err := c.Integrations.Get(context.Background(), "Fixture", nil)
			var apiErr *APIError
			if !errors.As(err, &apiErr) || apiErr.StatusCode != code || calls.Load() != 0 {
				t.Fatal("redirect was followed or misclassified")
			}
		})
	}
}
func TestGetRejections(t *testing.T) {
	for _, tc := range []struct {
		name, body string
		status     int
	}{
		{"foreign_org", "secret-body", 403},
		{"missing", "null", 200},
		{"malformed", "not-json-secret", 200},
		{"oversized", strings.Repeat("s", maxResponse+1), 200},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(tc.body))
			}))
			defer s.Close()
			c := testClient(t, s)
			got, err := c.Integrations.Get(context.Background(), "Fixture", nil)
			if tc.name == "missing" {
				if err != nil || got != nil {
					t.Fatal("null integration changed")
				}
				return
			}
			if err == nil || strings.Contains(err.Error(), tc.body) {
				t.Fatal("error missing or response body disclosed")
			}
		})
	}
}
func TestGetCancellation(t *testing.T) {
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { <-r.Context().Done() }))
	defer s.Close()
	c := testClient(t, s)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := c.Integrations.Get(ctx, "Fixture", nil); !errors.Is(err, context.Canceled) {
		t.Fatal("context cancellation lost")
	}
}

func TestCancellationWhileReadingBody(t *testing.T) {
	entered := make(chan struct{})
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.(http.Flusher).Flush()
		close(entered)
		<-r.Context().Done()
	}))
	defer s.Close()
	c := testClient(t, s)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { _, err := c.Integrations.Get(ctx, "Fixture", nil); done <- err }()
	<-entered
	cancel()
	if err := <-done; !errors.Is(err, context.Canceled) {
		t.Fatal("body read lost context cancellation")
	}
}
func TestProvisionValidation(t *testing.T) {
	for _, endpoint := range []string{"http://localhost", "https://user:pass@example.com", "https://example.com/path", "https://example.com?token=bad"} {
		if _, err := NewClient(Provision{Endpoint: endpoint, Bearer: "synthetic"}, nil); err == nil {
			t.Fatal("invalid endpoint accepted")
		}
	}
	if _, err := NewClient(Provision{Endpoint: "https://example.com"}, nil); err == nil {
		t.Fatal("missing credential accepted")
	}
}
func TestConfigPresence(t *testing.T) {
	for _, tc := range []struct {
		raw     string
		present bool
	}{{`null`, false}, {`"  "`, false}, {`[]`, false}, {`{}`, false}, {`false`, true}, {`0`, true}, {`[0]`, true}, {`{"x":0}`, true}} {
		i := Integration{Config: map[string]json.RawMessage{"key": json.RawMessage(tc.raw)}}
		if i.HasConfigValue("key") != tc.present {
			t.Fatalf("presence mismatch for %s", tc.raw)
		}
	}
}
