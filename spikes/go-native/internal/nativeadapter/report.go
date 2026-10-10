//go:build linux && amd64

package nativeadapter

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"reflect"

	"github.com/midtown-technology-group/bifrost-go/executionprofile"
)

var ReportRejected = errors.New("native runtime report rejected")

// ResultReport retains exactly the runtime payload submitted for a receipt.
// Construction and receipt observation are protocol operations, not admission,
// proof of actual execution, durable finalization or permission to replay work.
type ResultReport struct {
	decoded executionprofile.Decoded
	wire    []byte
	receipt map[string]any
}

type ReceiptEvidence struct{ DecisionID, Disposition, Winner string }

// SuccessReport validates one bounded child JSON value against the supplied
// accepted output schema. The caller must have proved launch and actual child
// completion separately. Arbitrary stderr/application errors are never echoed.
func SuccessReport(start executionprofile.Decoded, output, outputSchema []byte, messageID string, sequence int64) (*ResultReport, error) {
	validated, err := detachedFrame(start)
	if err != nil || validated.Frame["type"] != "Start" || len(output) == 0 || len(output) > 65536 || !strictJSONDepth(output, 64) {
		return nil, ReportRejected
	}
	schema, ok := parseWorkloadSchema(outputSchema)
	if !ok {
		return nil, ReportRejected
	}
	decoder := json.NewDecoder(bytes.NewReader(output))
	decoder.UseNumber()
	var value any
	if decoder.Decode(&value) != nil || !workloadMatches(schema, value) {
		return nil, ReportRejected
	}
	return resultReport(validated, messageID, sequence, map[string]any{"outcome": "success", "value": value})
}

// FailureReport carries only a fixed adapter failure, never tenant-controlled
// exception text, stderr, private delivery data or a claimed durable outcome.
func FailureReport(start executionprofile.Decoded, messageID string, sequence int64) (*ResultReport, error) {
	validated, err := detachedFrame(start)
	if err != nil || validated.Frame["type"] != "Start" {
		return nil, ReportRejected
	}
	return resultReport(validated, messageID, sequence, map[string]any{"outcome": "error", "error": map[string]any{"code": "NativeWorkloadFailed", "message": "native workload failed", "details": nil}})
}

func resultReport(start executionprofile.Decoded, messageID string, sequence int64, body map[string]any) (*ResultReport, error) {
	body["start_message_id"] = start.Frame["message_id"]
	frame := executionprofile.Frame{"protocol": executionprofile.Protocol, "type": "Result", "session_id": start.Frame["session_id"], "message_id": messageID, "sequence": sequence, "correlation_id": start.Frame["message_id"], "body": body}
	raw, err := json.Marshal(frame)
	if err != nil {
		return nil, ReportRejected
	}
	decoded, err := executionprofile.Decode(raw)
	if err != nil || messageID == start.Frame["message_id"] {
		return nil, ReportRejected
	}
	wire := make([]byte, 4+len(raw))
	binary.BigEndian.PutUint32(wire, uint32(len(raw)))
	copy(wire[4:], raw)
	return &ResultReport{decoded: decoded, wire: wire}, nil
}

func (r *ResultReport) PayloadSHA256() string { return r.decoded.PayloadSHA256() }
func (r *ResultReport) MessageID() string     { return r.decoded.Frame["message_id"].(string) }

// Write sends the original immutable framing/payload, never a reserialized view.
// A failed write is uncertain delivery: callers must not rerun the workload.
func (r *ResultReport) Write(writer io.Writer) error {
	if writer == nil {
		return ReportRejected
	}
	if _, err := io.Copy(writer, bytes.NewReader(r.wire)); err != nil {
		return ReportRejected
	}
	return nil
}

// ObserveReceipt checks the same session, Result ID and exact raw payload hash.
// Matching receipt bytes alone do not prove an authenticated coordinator or
// PostgreSQL durability. Conflicting repeated decisions reject without repair.
func (r *ResultReport) ObserveReceipt(received executionprofile.Decoded) (ReceiptEvidence, error) {
	decoded, err := detachedFrame(received)
	if err != nil || decoded.Frame["type"] != "ResultReceipt" || decoded.Frame["session_id"] != r.decoded.Frame["session_id"] || decoded.Frame["correlation_id"] != r.MessageID() {
		return ReceiptEvidence{}, ReportRejected
	}
	body := decoded.Frame["body"].(map[string]any)
	if body["result_message_id"] != r.MessageID() || body["result_sha256"] != r.PayloadSHA256() {
		return ReceiptEvidence{}, ReportRejected
	}
	if r.receipt != nil && !reflect.DeepEqual(body, r.receipt) {
		return ReceiptEvidence{}, ReportRejected
	}
	r.receipt = cloneProtocolValue(body).(map[string]any)
	return ReceiptEvidence{DecisionID: body["decision_id"].(string), Disposition: body["disposition"].(string), Winner: body["winner"].(string)}, nil
}
