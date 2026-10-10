//go:build linux && amd64

package nativeadapter

import (
	"bytes"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/midtown-technology-group/bifrost-go/executionprofile"
)

const offerFixtureID = "00000000-0000-0000-0000-000000000030"
const preparedFixtureID = "00000000-0000-0000-0000-000000000031"

func transportFixture(t *testing.T) (executionprofile.Frame, executionprofile.Decoded, PreparationInputs, time.Time) {
	t.Helper()
	prepare, accepted, now := preparationFixture(t)
	selectID := "00000000-0000-0000-0000-000000000032"
	selectFrame := executionprofile.Frame{"protocol": executionprofile.Protocol, "type": "Select", "session_id": accepted.Binding["session_id"], "message_id": selectID, "sequence": 1, "correlation_id": offerFixtureID, "body": map[string]any{"protocol": executionprofile.Protocol, "capability": executionprofile.Profile, "artifact_class": "native-executable/v1"}}
	prepare.Frame["correlation_id"] = selectID
	prepare.Frame["sequence"] = json.Number("2")
	return selectFrame, prepare, accepted, now
}

func transportWire(t *testing.T, frames ...executionprofile.Frame) []byte {
	t.Helper()
	var wire bytes.Buffer
	for _, frame := range frames {
		if err := executionprofile.Write(&wire, frame); err != nil {
			t.Fatal(err)
		}
	}
	return wire.Bytes()
}

func TestPrepareTransportCommonFrames(t *testing.T) {
	selected, prepare, accepted, now := transportFixture(t)
	var output bytes.Buffer
	prepared, err := PrepareTransport(bytes.NewReader(transportWire(t, selected, prepare.Frame)), &output, accepted, offerFixtureID, preparedFixtureID, func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	defer prepared.Close()
	offer, err := executionprofile.Read(&output)
	if err != nil || offer.Frame["type"] != "Offer" || offer.Frame["message_id"] != offerFixtureID {
		t.Fatal("missing valid Offer")
	}
	reply, err := executionprofile.Read(&output)
	if err != nil || reply.Frame["type"] != "Prepared" || reply.Frame["correlation_id"] != prepare.Frame["message_id"] || reply.Frame["message_id"] != preparedFixtureID {
		t.Fatal("missing correlated Prepared")
	}
	if output.Len() != 0 {
		t.Fatal("unexpected outbound data")
	}
	if _, err := NewNativeFrontier(prepared, accepted.Binding, 2); err != nil {
		t.Fatal("prepared stream cannot continue to runtime frontier")
	}
}

func TestPrepareTransportRejectsNegotiationDrift(t *testing.T) {
	for _, name := range []string{"select-session", "select-correlation", "select-class", "prepare-correlation", "parent-sequence", "duplicate-id", "expired", "truncated"} {
		t.Run(name, func(t *testing.T) {
			selected, prepare, accepted, now := transportFixture(t)
			switch name {
			case "select-session":
				selected["session_id"] = "00000000-0000-0000-0000-000000000099"
			case "select-correlation":
				selected["correlation_id"] = "00000000-0000-0000-0000-000000000099"
			case "select-class":
				selected["body"].(map[string]any)["artifact_class"] = "interpreted-runtime/v1"
			case "prepare-correlation":
				prepare.Frame["correlation_id"] = "00000000-0000-0000-0000-000000000099"
			case "parent-sequence":
				prepare.Frame["sequence"] = json.Number("1")
			case "duplicate-id":
				prepare.Frame["message_id"] = selected["message_id"]
			case "expired":
				now = now.Add(time.Minute)
			}
			wire := transportWire(t, selected, prepare.Frame)
			if name == "truncated" {
				wire = wire[:len(wire)-1]
			}
			var output bytes.Buffer
			prepared, err := PrepareTransport(bytes.NewReader(wire), &output, accepted, offerFixtureID, preparedFixtureID, func() time.Time { return now })
			if prepared != nil {
				_ = prepared.Close()
				t.Fatal("failed exchange retained preparation")
			}
			if err != PreparationRejected {
				t.Fatal("invalid exchange accepted")
			}
			offer, err := executionprofile.Read(&output)
			if err != nil || offer.Frame["type"] != "Offer" || output.Len() != 0 {
				t.Fatal("Prepared emitted after invalid exchange")
			}
		})
	}
}

type rejectPreparedWriter struct {
	bytes.Buffer
	writes int
}

func (w *rejectPreparedWriter) Write(raw []byte) (int, error) {
	// Each frame is written together. Accept Offer, fail the Prepared write.
	w.writes++
	if w.writes > 1 {
		return 0, errors.New("synthetic transport failure")
	}
	return w.Buffer.Write(raw)
}

func TestPrepareTransportDoesNotReturnOnLostPreparedWrite(t *testing.T) {
	selected, prepare, accepted, now := transportFixture(t)
	writer := &rejectPreparedWriter{}
	prepared, err := PrepareTransport(bytes.NewReader(transportWire(t, selected, prepare.Frame)), writer, accepted, offerFixtureID, preparedFixtureID, func() time.Time { return now })
	if prepared != nil {
		_ = prepared.Close()
		t.Fatal("returned preparation after failed acknowledgement")
	}
	if err != PreparationRejected {
		t.Fatal("failed Prepared write accepted")
	}
}
