//go:build linux && amd64

package nativeadapter

import (
	"encoding/json"
	"errors"
	"reflect"
	"time"

	"github.com/midtown-technology-group/bifrost-go/executionprofile"
)

var FrontierRejected = errors.New("native runtime frontier rejected")

// NativeFrontier observes common parent messages after Prepared. It owns no
// database or launch authority. A matching Start/Provision is necessary evidence
// for the adapter, never proof that the guardian committed release or may spawn.
// The protocol loop must serialize calls and close it on transport loss.
type NativeFrontier struct {
	prepared   *PreparedNative
	binding    map[string]any
	sessionID  string
	sequence   int64
	messageIDs map[string]bool
	start      executionprofile.Decoded
	provision  executionprofile.Decoded
	deadline   time.Time
	closed     bool
}

func NewNativeFrontier(prepared *PreparedNative, binding map[string]any, parentSequence int64) (*NativeFrontier, error) {
	// A separately supplied binding must equal the independently retained Prepare.
	if prepared == nil || prepared.executable == nil || parentSequence != prepared.parentSequence || !reflect.DeepEqual(binding, prepared.binding) {
		return nil, FrontierRejected
	}
	return &NativeFrontier{prepared: prepared, binding: cloneProtocolValue(prepared.binding).(map[string]any), sessionID: prepared.sessionID, sequence: parentSequence, messageIDs: map[string]bool{prepared.prepareID: true}, deadline: prepared.deadline}, nil
}

func detachedFrame(received executionprofile.Decoded) (executionprofile.Decoded, error) {
	raw, err := json.Marshal(received.Frame)
	if err != nil {
		return executionprofile.Decoded{}, FrontierRejected
	}
	decoded, err := executionprofile.Decode(raw)
	if err != nil {
		return executionprofile.Decoded{}, FrontierRejected
	}
	return decoded, nil
}

// Observe allows Provision before Start, as the common contract permits. Both
// must identify the same committed Start before material can even be read.
// Invalid or duplicate frames leave the last valid frontier unchanged.
func (f *NativeFrontier) Observe(received executionprofile.Decoded, now time.Time) error {
	decoded, err := detachedFrame(received)
	if err != nil || f.closed || decoded.Frame["session_id"] != f.sessionID {
		return FrontierRejected
	}
	sequence, err := decoded.Frame["sequence"].(json.Number).Int64()
	id := decoded.Frame["message_id"].(string)
	if err != nil || sequence <= f.sequence || f.messageIDs[id] {
		return FrontierRejected
	}
	body := decoded.Frame["body"].(map[string]any)
	deadline := f.deadline
	switch decoded.Frame["type"] {
	case "Start":
		if f.start.Frame != nil || body["prepare_message_id"] != f.prepared.prepareID || !now.Before(f.deadline) {
			return FrontierRejected
		}
		budget, ok := body["remaining_run_ms"].(json.Number)
		if !ok {
			return FrontierRejected
		} // This slice has no unbounded/renewal grant.
		milliseconds, err := budget.Int64()
		if err != nil || milliseconds <= 0 {
			return FrontierRejected
		}
		// Compare before multiplying, to avoid overflow for valid large wire budgets.
		if milliseconds < f.deadline.Sub(now).Milliseconds() {
			deadline = now.Add(time.Duration(milliseconds) * time.Millisecond)
		}
		if f.provision.Frame != nil && f.provision.Frame["body"].(map[string]any)["committed_start_id"] != body["committed_start_id"] {
			return FrontierRejected
		}
		f.start = decoded
	case "Provision":
		if f.provision.Frame != nil || body["prepare_message_id"] != f.prepared.prepareID || !reflect.DeepEqual(body["binding"], f.binding) || !now.Before(f.deadline) {
			return FrontierRejected
		}
		expiry, err := time.Parse(time.RFC3339Nano, body["expires_at"].(string))
		capabilities := body["capabilities"].([]any)
		if err != nil || !now.Before(expiry) || len(capabilities) != 1 || capabilities[0] != "integration-get" {
			return FrontierRejected
		}
		if f.start.Frame != nil && f.start.Frame["body"].(map[string]any)["committed_start_id"] != body["committed_start_id"] {
			return FrontierRejected
		}
		f.provision = decoded
	case "Cancel":
		f.closed = true // Local observation only; never finalizes durable Cancelled.
	default:
		return FrontierRejected
	}
	f.deadline = deadline
	f.sequence = sequence
	f.messageIDs[id] = true
	return nil
}

// MaterialExpectation returns a detached Provision only after matching finite
// Start and Provision. This is not a release permit or issuer authentication.
func (f *NativeFrontier) MaterialExpectation(now time.Time) (executionprofile.Decoded, time.Time, error) {
	if f.closed || f.start.Frame == nil || f.provision.Frame == nil || !now.Before(f.deadline) {
		return executionprofile.Decoded{}, time.Time{}, FrontierRejected
	}
	expiry, err := time.Parse(time.RFC3339Nano, f.provision.Frame["body"].(map[string]any)["expires_at"].(string))
	if err != nil || !now.Before(expiry) {
		return executionprofile.Decoded{}, time.Time{}, FrontierRejected
	}
	deadline := f.deadline
	if expiry.Before(deadline) {
		deadline = expiry
	}
	decoded, err := detachedFrame(f.provision)
	return decoded, deadline, err
}

func (f *NativeFrontier) Close() { f.closed = true }
