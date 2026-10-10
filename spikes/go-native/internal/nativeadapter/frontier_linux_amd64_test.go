//go:build linux && amd64

package nativeadapter

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/midtown-technology-group/bifrost-go/executionprofile"
)

func frontierFixture(t *testing.T) (*NativeFrontier, executionprofile.Decoded, executionprofile.Decoded, time.Time) {
	t.Helper()
	prepare, accepted, now := preparationFixture(t)
	prepared, err := PrepareNative(prepare, accepted, now)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = prepared.Close() })
	frontier, err := NewNativeFrontier(prepared, accepted.Binding, 1)
	if err != nil {
		t.Fatal(err)
	}
	id := "00000000-0000-0000-0000-000000000020"
	frame := func(kind, message string, sequence int, body map[string]any) executionprofile.Decoded {
		raw, err := json.Marshal(executionprofile.Frame{"protocol": executionprofile.Protocol, "type": kind, "session_id": prepared.sessionID, "message_id": message, "sequence": sequence, "correlation_id": prepared.prepareID, "body": body})
		if err != nil {
			t.Fatal(err)
		}
		decoded, err := executionprofile.Decode(raw)
		if err != nil {
			t.Fatal(err)
		}
		return decoded
	}
	start := frame("Start", "00000000-0000-0000-0000-000000000021", 2, map[string]any{"prepare_message_id": prepared.prepareID, "committed_start_id": id, "remaining_run_ms": 5000})
	provision := frame("Provision", "00000000-0000-0000-0000-000000000022", 3, map[string]any{"prepare_message_id": prepared.prepareID, "committed_start_id": id, "binding": accepted.Binding, "grant_id": id, "delivery_id": id, "expires_at": "2030-01-01T00:01:00Z", "operations_digest": "sha256:" + strings.Repeat("a", 64), "capabilities": []string{"integration-get"}})
	return frontier, start, provision, now
}

func TestNativeFrontierRequiresBothObservations(t *testing.T) {
	for _, order := range []string{"start-first", "provision-first"} {
		t.Run(order, func(t *testing.T) {
			f, start, provision, now := frontierFixture(t)
			if _, _, err := f.MaterialExpectation(now); err != FrontierRejected {
				t.Fatal("material before observations")
			}
			first, second := start, provision
			if order == "provision-first" {
				provision.Frame["sequence"] = json.Number("2")
				start.Frame["sequence"] = json.Number("3")
				first, second = provision, start
			}
			if err := f.Observe(first, now); err != nil {
				t.Fatal(err)
			}
			if _, _, err := f.MaterialExpectation(now); err != FrontierRejected {
				t.Fatal("material before both observations")
			}
			if err := f.Observe(second, now); err != nil {
				t.Fatal(err)
			}
			expected, deadline, err := f.MaterialExpectation(now)
			if err != nil || !reflect.DeepEqual(expected.Frame, provision.Frame) || !deadline.Equal(now.Add(5*time.Second)) {
				t.Fatal("incorrect material expectation or budget")
			}
			expected.Frame["body"].(map[string]any)["delivery_id"] = "mutated"
			again, _, err := f.MaterialExpectation(now)
			if err != nil || again.Frame["body"].(map[string]any)["delivery_id"] == "mutated" {
				t.Fatal("mutable retained provision")
			}
			if _, _, err := f.MaterialExpectation(now.Add(5 * time.Second)); err != FrontierRejected {
				t.Fatal("expired budget")
			}
		})
	}
}

func TestNativeFrontierRejectsDriftAndReplay(t *testing.T) {
	for _, name := range []string{"wrong-session", "wrong-prepare", "wrong-binding", "wrong-start", "expiry", "capabilities", "sequence", "duplicate-id", "null-budget", "duplicate-start", "closed"} {
		t.Run(name, func(t *testing.T) {
			f, start, provision, now := frontierFixture(t)
			if err := f.Observe(start, now); err != nil {
				t.Fatal(err)
			}
			body := provision.Frame["body"].(map[string]any)
			candidate := provision
			switch name {
			case "wrong-session":
				candidate.Frame["session_id"] = "00000000-0000-0000-0000-000000000099"
			case "wrong-prepare":
				body["prepare_message_id"] = "00000000-0000-0000-0000-000000000099"
				candidate.Frame["correlation_id"] = body["prepare_message_id"]
			case "wrong-binding":
				body["binding"].(map[string]any)["attempt_id"] = "00000000-0000-0000-0000-000000000099"
			case "wrong-start":
				body["committed_start_id"] = "00000000-0000-0000-0000-000000000099"
			case "expiry":
				body["expires_at"] = "2030-01-01T00:00:00Z"
			case "capabilities":
				body["capabilities"] = []string{"integration-get", "admin"}
			case "sequence":
				candidate.Frame["sequence"] = json.Number("2")
			case "duplicate-id":
				candidate.Frame["message_id"] = start.Frame["message_id"]
			case "null-budget":
				f, start, _, now = frontierFixture(t)
				candidate = start
				candidate.Frame["body"].(map[string]any)["remaining_run_ms"] = nil
			case "duplicate-start":
				candidate = start
				candidate.Frame["sequence"] = json.Number("3")
				candidate.Frame["message_id"] = "00000000-0000-0000-0000-000000000099"
			case "closed":
				f.Close()
			}
			before := f.sequence
			if err := f.Observe(candidate, now); err != FrontierRejected {
				t.Fatal("invalid observation accepted")
			}
			if f.sequence != before || f.provision.Frame != nil {
				t.Fatal("invalid observation changed retained frontier")
			}
		})
	}
}

func TestNativeFrontierCancelPreventsLaterMaterial(t *testing.T) {
	f, start, provision, now := frontierFixture(t)
	if err := f.Observe(start, now); err != nil {
		t.Fatal(err)
	}
	if err := f.Observe(provision, now); err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(executionprofile.Frame{"protocol": executionprofile.Protocol, "type": "Cancel", "session_id": f.sessionID, "message_id": "00000000-0000-0000-0000-000000000023", "sequence": 4, "correlation_id": nil, "body": map[string]any{"cancel_id": "00000000-0000-0000-0000-000000000024", "reason": "requested", "grace_ms": 0}})
	if err != nil {
		t.Fatal(err)
	}
	cancel, err := executionprofile.Decode(raw)
	if err != nil {
		t.Fatal(err)
	}
	if err := f.Observe(cancel, now); err != nil {
		t.Fatal(err)
	}
	if _, _, err := f.MaterialExpectation(now); err != FrontierRejected {
		t.Fatal("material after Cancel")
	}
	if err := f.Observe(start, now); err != FrontierRejected {
		t.Fatal("reopened cancelled frontier")
	}
}
