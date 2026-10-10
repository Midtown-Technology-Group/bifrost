//go:build linux && amd64

package nativeadapter

import (
	"bytes"
	"encoding/json"
	"io"
	"strings"
	"testing"

	"github.com/midtown-technology-group/bifrost-go/executionprofile"
)

const resultFixtureID = "00000000-0000-0000-0000-000000000040"

func reportFixture(t *testing.T) (*ResultReport, executionprofile.Decoded) {
	t.Helper()
	_, start, _, _ := frontierFixture(t)
	report, err := SuccessReport(start, []byte(` {"ready":true,"missing_keys":null} `), []byte(`{"type":"object","required":["ready","missing_keys"],"additionalProperties":false,"properties":{"ready":{"type":"boolean"},"missing_keys":{"type":["array","null"],"items":{"type":"string"}}}}`), resultFixtureID, 3)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(executionprofile.Frame{"protocol": executionprofile.Protocol, "type": "ResultReceipt", "session_id": start.Frame["session_id"], "message_id": "00000000-0000-0000-0000-000000000041", "sequence": 4, "correlation_id": resultFixtureID, "body": map[string]any{"result_message_id": resultFixtureID, "result_sha256": report.PayloadSHA256(), "decision_id": "00000000-0000-0000-0000-000000000042", "disposition": "accepted", "winner": "result"}})
	if err != nil {
		t.Fatal(err)
	}
	receipt, err := executionprofile.Decode(raw)
	if err != nil {
		t.Fatal(err)
	}
	return report, receipt
}

func TestResultReportRetainsExactPayloadAndReceipt(t *testing.T) {
	report, receipt := reportFixture(t)
	var first, second bytes.Buffer
	if report.Write(&first) != nil || report.Write(&second) != nil || !bytes.Equal(first.Bytes(), second.Bytes()) {
		t.Fatal("report bytes changed")
	}
	decoded, err := executionprofile.Read(&first)
	if err != nil || decoded.PayloadSHA256() != report.PayloadSHA256() || decoded.Frame["type"] != "Result" {
		t.Fatal("wire payload drift")
	}
	decision, err := report.ObserveReceipt(receipt)
	if err != nil || decision.Disposition != "accepted" || decision.Winner != "result" {
		t.Fatal("matching receipt rejected")
	}
	if again, err := report.ObserveReceipt(receipt); err != nil || again != decision {
		t.Fatal("same receipt decision not retained")
	}
	receipt.Frame["body"].(map[string]any)["decision_id"] = "00000000-0000-0000-0000-000000000099"
	if _, err := report.ObserveReceipt(receipt); err != ReportRejected {
		t.Fatal("conflicting decision accepted")
	}
}

func TestResultReceiptRejectsIdentityAndHashDrift(t *testing.T) {
	for _, name := range []string{"session", "correlation", "result-id", "hash", "winner"} {
		t.Run(name, func(t *testing.T) {
			report, receipt := reportFixture(t)
			body := receipt.Frame["body"].(map[string]any)
			switch name {
			case "session":
				receipt.Frame["session_id"] = "00000000-0000-0000-0000-000000000099"
			case "correlation":
				receipt.Frame["correlation_id"] = "00000000-0000-0000-0000-000000000099"
			case "result-id":
				body["result_message_id"] = "00000000-0000-0000-0000-000000000099"
				receipt.Frame["correlation_id"] = body["result_message_id"]
			case "hash":
				body["result_sha256"] = strings.Repeat("0", 64)
			case "winner":
				body["winner"] = "cancel" // accepted/cancel is structurally illegal.
			}
			if _, err := report.ObserveReceipt(receipt); err != ReportRejected || report.receipt != nil {
				t.Fatal("invalid receipt changed observation")
			}
		})
	}
}

func TestSuccessReportRejectsAmbiguousOrInvalidOutput(t *testing.T) {
	_, start, _, _ := frontierFixture(t)
	schema := []byte(`{"type":"object","required":["ready"],"additionalProperties":false,"properties":{"ready":{"type":"boolean"}}}`)
	for _, raw := range []string{`{"ready":true,"ready":false}`, `{"ready":true}{}`, `{"ready":"true"}`, `{"ready":true,"secret":"private"}`, `{"ready":true,"x":"\ud800"}`, strings.Repeat("x", 65537)} {
		if report, err := SuccessReport(start, []byte(raw), schema, resultFixtureID, 3); err != ReportRejected || report != nil {
			t.Fatal("invalid output accepted")
		}
	}
}

type shortReportWriter struct{}

func (shortReportWriter) Write(raw []byte) (int, error) { return len(raw) - 1, nil }

func TestFailedResultWriteIsUncertainDelivery(t *testing.T) {
	report, _ := reportFixture(t)
	if report.Write(shortReportWriter{}) != ReportRejected {
		t.Fatal("short report write accepted")
	}
	var retry bytes.Buffer
	if report.Write(&retry) != nil {
		t.Fatal("retained report unavailable for owner readback")
	}
	decoded, err := executionprofile.Read(&retry)
	if err != nil || decoded.PayloadSHA256() != report.PayloadSHA256() {
		t.Fatal("uncertain write changed original report")
	}
	if _, err := executionprofile.Read(&retry); err != io.EOF {
		t.Fatal("extra report bytes")
	}
}

func TestFailureReportUsesStaticError(t *testing.T) {
	_, start, _, _ := frontierFixture(t)
	report, err := FailureReport(start, resultFixtureID, 3)
	if err != nil {
		t.Fatal(err)
	}
	var wire bytes.Buffer
	if report.Write(&wire) != nil {
		t.Fatal("failure serialization rejected")
	}
	decoded, err := executionprofile.Read(&wire)
	if err != nil {
		t.Fatal(err)
	}
	body := decoded.Frame["body"].(map[string]any)
	if body["outcome"] != "error" || body["error"].(map[string]any)["message"] != "native workload failed" {
		t.Fatal("failure text drift")
	}
}
