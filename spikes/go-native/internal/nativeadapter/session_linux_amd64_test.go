//go:build linux && amd64

package nativeadapter

import (
	"context"
	"encoding/json"
	"net"
	"os"
	"syscall"
	"testing"
	"time"

	"github.com/midtown-technology-group/bifrost-go/executionprofile"
)

func TestSessionCancelBeforeStartOrWhileMaterialIsWithheld(t *testing.T) {
	for _, phase := range []string{"before-start", "withheld-material"} {
		t.Run(phase, func(t *testing.T) {
			selected, prepare, accepted, _ := transportFixture(t)
			parent, adapter := net.Pipe()
			defer parent.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			deadline, _ := ctx.Deadline()
			_ = parent.SetDeadline(deadline)
			reader, writer, err := os.Pipe()
			if err != nil {
				t.Fatal(err)
			}
			defer writer.Close()
			fd := transferDescriptor(t, reader)
			// The caller owns the unused descriptor before Start. During delivery,
			// ReadInheritedContext owns it and closes it when this session cancels.
			if phase == "before-start" {
				defer syscall.Close(int(fd))
			}
			done := make(chan error, 1)
			go func() {
				done <- RunInheritedSession(ctx, SessionTransport{Parent: adapter, Runtime: adapter, MaterialFD: fd}, accepted)
			}()
			offer, err := executionprofile.Read(parent)
			if err != nil {
				t.Fatal(err)
			}
			selected["correlation_id"] = offer.Frame["message_id"]
			if err := executionprofile.Write(parent, selected); err != nil {
				t.Fatal(err)
			}
			if err := executionprofile.Write(parent, prepare.Frame); err != nil {
				t.Fatal(err)
			}
			prepared, err := executionprofile.Read(parent)
			if err != nil || prepared.Frame["type"] != "Prepared" {
				t.Fatal("no Prepared")
			}
			sequence := 3
			if phase == "withheld-material" {
				_, start, provision, _ := frontierFixture(t)
				start.Frame["session_id"] = prepare.Frame["session_id"]
				start.Frame["correlation_id"] = prepare.Frame["message_id"]
				start.Frame["sequence"] = json.Number("3")
				start.Frame["body"].(map[string]any)["prepare_message_id"] = prepare.Frame["message_id"]
				provision.Frame["session_id"] = prepare.Frame["session_id"]
				provision.Frame["correlation_id"] = prepare.Frame["message_id"]
				provision.Frame["sequence"] = json.Number("4")
				provision.Frame["body"].(map[string]any)["prepare_message_id"] = prepare.Frame["message_id"]
				if err := executionprofile.Write(parent, start.Frame); err != nil {
					t.Fatal(err)
				}
				if err := executionprofile.Write(parent, provision.Frame); err != nil {
					t.Fatal(err)
				}
				sequence = 5
			}
			cancelID := "00000000-0000-0000-0000-000000000050"
			frame := executionprofile.Frame{"protocol": executionprofile.Protocol, "type": "Cancel", "session_id": prepare.Frame["session_id"], "message_id": "00000000-0000-0000-0000-000000000051", "sequence": sequence, "correlation_id": nil, "body": map[string]any{"cancel_id": cancelID, "reason": "requested", "grace_ms": 100}}
			if err := executionprofile.Write(parent, frame); err != nil {
				t.Fatal(err)
			}
			stopped, err := executionprofile.Read(parent)
			if err != nil || stopped.Frame["type"] != "Stopped" {
				t.Fatal("cancel did not produce advisory Stopped")
			}
			body := stopped.Frame["body"].(map[string]any)
			if body["reason"] != "cancelled" || body["cancel_id"] != cancelID || body["result_message_id"] != nil {
				t.Fatal("incorrect stop observation")
			}
			if err := <-done; err != nil {
				t.Fatal(err)
			}
		})
	}
}
