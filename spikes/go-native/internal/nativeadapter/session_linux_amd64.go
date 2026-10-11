//go:build linux && amd64

package nativeadapter

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"syscall"
	"time"

	"github.com/midtown-technology-group/bifrost-go/executionprofile"
)

// SessionTransport is owned by the trusted guardian. It must arrange unique
// inherited pipe custody, accepted bundle provenance and an observed Rust release
// commit BEFORE delivering private material. No wire UUID is a launch permit.
// This candidate does not establish those prerequisites itself.
type SessionTransport struct {
	Parent     io.ReadCloser
	Runtime    io.Writer
	MaterialFD uintptr
}

func sessionMessageID() (string, error) {
	var id [16]byte
	if _, err := rand.Read(id[:]); err != nil {
		return "", PreparationRejected
	}
	id[6] = (id[6] & 15) | 64
	id[8] = (id[8] & 63) | 128
	return fmt.Sprintf("%x-%x-%x-%x-%x", id[:4], id[4:6], id[6:8], id[8:10], id[10:]), nil
}

// boundedOutput prevents a tenant from exhausting adapter memory. stderr is
// discarded separately and cannot enter common protocol or Result evidence.
type boundedOutput struct{ bytes.Buffer }

func (b *boundedOutput) Write(raw []byte) (int, error) {
	if len(raw) > 65536-b.Len() {
		return 0, ReportRejected
	}
	return b.Buffer.Write(raw)
}

func executeChild(ctx context.Context, prepared *PreparedNative, sdk SDKConfiguration) ([]byte, error) {
	reader, writer, err := os.Pipe()
	if err != nil {
		return nil, ReportRejected
	}
	defer reader.Close()
	defer writer.Close()
	// Configuration goes only to FD3. No writer is inherited by the tenant.
	raw, err := json.Marshal(sdk)
	if err != nil || len(raw) > MaxDeliveryBytes || ctx.Err() != nil {
		return nil, ReportRejected
	}
	cmd := exec.CommandContext(ctx, prepared.executable.Path(), "--local")
	cmd.Env = []string{"PATH=/nonexistent"}
	cmd.Stdin = bytes.NewReader(prepared.input)
	cmd.ExtraFiles = []*os.File{reader}
	output := &boundedOutput{}
	cmd.Stdout = output
	cmd.Stderr = io.Discard
	// Rust must retain the enclosing cgroup/source-consumer obligation. Waiting
	// here proves only this direct child; descendants are NOT declared drained.
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.WaitDelay = 100 * time.Millisecond
	cmd.Cancel = func() error { return cmd.Process.Signal(syscall.SIGTERM) }
	if err := cmd.Start(); err != nil {
		return nil, ReportRejected
	}
	_ = reader.Close()
	delivered := make(chan error, 1)
	go func() {
		_, err := io.Copy(writer, bytes.NewReader(append(raw, '\n')))
		_ = writer.Close()
		delivered <- err
	}()
	waitErr := cmd.Wait()
	_ = writer.Close() // Unblock delivery if the child never consumed FD3.
	if deliveryErr := <-delivered; waitErr != nil || deliveryErr != nil {
		return nil, ReportRejected
	}
	return append([]byte(nil), output.Bytes()...), nil
}

// RunInheritedSession is a single common protocol session, never a coordinator.
// It launches no tenant before matching Start/Provision and one private delivery;
// the guardian must withhold that delivery until its release commit is observed.
// Cancel/transport loss stops the direct child. Rust alone decides durable outcome
// and must prove process/descendant/source cleanup, including uncertain spawn.
func RunInheritedSession(ctx context.Context, transport SessionTransport, accepted PreparationInputs) error {
	if transport.Parent == nil || transport.Runtime == nil || transport.MaterialFD < 3 {
		return PreparationRejected
	}
	if deadline, finite := ctx.Deadline(); !finite || !deadline.After(time.Now()) {
		return PreparationRejected
	}
	ctx, finish := context.WithCancel(ctx)
	defer finish()
	defer transport.Parent.Close()
	go func(lifetime context.Context) { <-lifetime.Done(); _ = transport.Parent.Close() }(ctx)
	offerID, err := sessionMessageID()
	if err != nil {
		return err
	}
	preparedID, err := sessionMessageID()
	if err != nil {
		return err
	}
	prepared, err := PrepareTransport(transport.Parent, transport.Runtime, accepted, offerID, preparedID, time.Now)
	if err != nil {
		return err
	}
	defer prepared.Close()
	frontier, err := NewNativeFrontier(prepared, accepted.Binding, prepared.parentSequence)
	if err != nil {
		return err
	}
	defer frontier.Close()
	sequence := int64(3)
	stopped := func(cancelID any, reason string, resultID any) error {
		id, err := sessionMessageID()
		if err != nil {
			return err
		}
		var startID any
		if frontier.start.Frame != nil {
			startID = frontier.start.Frame["message_id"]
		}
		return executionprofile.Write(transport.Runtime, executionprofile.Frame{"protocol": executionprofile.Protocol, "type": "Stopped", "session_id": prepared.sessionID, "message_id": id, "sequence": sequence, "correlation_id": nil, "body": map[string]any{"start_message_id": startID, "cancel_id": cancelID, "reason": reason, "result_message_id": resultID, "error": nil}})
	}
	ctx, cancel := context.WithDeadline(ctx, prepared.deadline)
	defer cancel()
	type observed struct {
		frame executionprofile.Decoded
		err   error
	}
	frames := make(chan observed)
	go func() {
		for {
			frame, err := executionprofile.Read(transport.Parent)
			select {
			case frames <- observed{frame, err}:
			case <-ctx.Done():
				return
			}
			if err != nil {
				return
			}
		}
	}()
	for frontier.start.Frame == nil || frontier.provision.Frame == nil {
		select {
		case <-ctx.Done():
			return FrontierRejected
		case received := <-frames:
			if received.err != nil || frontier.Observe(received.frame, time.Now()) != nil {
				return FrontierRejected
			}
			if frontier.closed {
				return stopped(received.frame.Frame["body"].(map[string]any)["cancel_id"], "cancelled", nil)
			}
		}
	}
	provision, deadline, err := frontier.MaterialExpectation(time.Now())
	if err != nil {
		return err
	}
	childCtx, stopChild := context.WithDeadline(ctx, deadline)
	defer stopChild()
	type material struct {
		sdk SDKConfiguration
		err error
	}
	delivery := make(chan material, 1)
	go func() {
		reader := &DeliveryReader{}
		sdk, err := reader.ReadInheritedContext(childCtx, transport.MaterialFD, provision, deadline)
		delivery <- material{sdk, err}
	}()
	var sdk SDKConfiguration
	select {
	case <-childCtx.Done():
		return DeliveryRejected
	case received := <-frames:
		if received.err == nil && frontier.Observe(received.frame, time.Now()) == nil && frontier.closed {
			return stopped(received.frame.Frame["body"].(map[string]any)["cancel_id"], "cancelled", nil)
		}
		return FrontierRejected // No further frame is legal before material.
	case value := <-delivery:
		if value.err != nil {
			return value.err
		}
		sdk = value.sdk
	}
	// Recheck after material; it may have arrived at an expired frontier.
	if _, _, err := frontier.MaterialExpectation(time.Now()); err != nil {
		return err
	}
	type completion struct {
		output []byte
		err    error
	}
	done := make(chan completion, 1)
	go func() { output, err := executeChild(childCtx, prepared, sdk); done <- completion{output, err} }()
	started := time.Now()
	heartbeat := time.NewTicker(time.Second)
	defer heartbeat.Stop()
	var report *ResultReport
	for {
		select {
		case <-childCtx.Done():
			stopChild()
			<-done // Direct child wait remains mandatory; no descendant claim.
			return FrontierRejected
		case received := <-frames:
			if received.err != nil || frontier.Observe(received.frame, time.Now()) != nil {
				stopChild()
				<-done
				return FrontierRejected
			}
			if frontier.closed {
				stopChild()
				<-done
				return stopped(received.frame.Frame["body"].(map[string]any)["cancel_id"], "cancelled", nil)
			}
		case <-heartbeat.C:
			id, err := sessionMessageID()
			if err != nil {
				stopChild()
				<-done
				return err
			}
			frame := executionprofile.Frame{"protocol": executionprofile.Protocol, "type": "Heartbeat", "session_id": prepared.sessionID, "message_id": id, "sequence": sequence, "correlation_id": nil, "body": map[string]any{"start_message_id": frontier.start.Frame["message_id"], "state": "executing", "monotonic_elapsed_ms": time.Since(started).Milliseconds()}}
			sequence++
			if executionprofile.Write(transport.Runtime, frame) != nil {
				stopChild()
				<-done
				return ReportRejected
			}
		case completed := <-done:
			// Fixed adapter observation contains no input, output, stderr, SDK
			// configuration or credentials. It claims no durable outcome.
			logID, err := sessionMessageID()
			if err != nil {
				return err
			}
			logFrame := executionprofile.Frame{"protocol": executionprofile.Protocol, "type": "LogBatch", "session_id": prepared.sessionID, "message_id": logID, "sequence": sequence, "correlation_id": frontier.start.Frame["message_id"], "body": map[string]any{"start_message_id": frontier.start.Frame["message_id"], "batch_sequence": 1, "entries": []any{map[string]any{"level": "info", "message": "Workflow process exited"}}}}
			if executionprofile.Write(transport.Runtime, logFrame) != nil {
				return ReportRejected
			}
			sequence++
			id, err := sessionMessageID()
			if err != nil {
				return err
			}
			if completed.err == nil {
				report, err = SuccessReport(frontier.start, completed.output, prepared.outputSchema, id, sequence)
			} else {
				report, err = FailureReport(frontier.start, id, sequence)
			}
			if err != nil || report.Write(transport.Runtime) != nil {
				return ReportRejected
			}
			sequence++
			for {
				select {
				case <-ctx.Done():
					return ReportRejected
				case received := <-frames:
					if received.err != nil {
						return ReportRejected
					}
					if received.frame.Frame["type"] == "Cancel" {
						if frontier.Observe(received.frame, time.Now()) != nil {
							return FrontierRejected
						}
						continue
					}
					parentSequence, sequenceErr := received.frame.Frame["sequence"].(json.Number).Int64()
					messageID := received.frame.Frame["message_id"].(string)
					if sequenceErr != nil || parentSequence <= frontier.sequence || frontier.messageIDs[messageID] {
						return ReportRejected
					}
					_, err := report.ObserveReceipt(received.frame)
					if err != nil {
						return err
					}
					return stopped(nil, "completed", report.MessageID()) // Advisory direct-child observation only.
				}
			}
		}
	}
}
