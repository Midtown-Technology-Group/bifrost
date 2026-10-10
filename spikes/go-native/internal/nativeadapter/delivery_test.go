package nativeadapter

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/binary"
	"encoding/json"
	"encoding/pem"
	"io"
	"math/big"
	"strings"
	"testing"
	"time"

	"github.com/midtown-technology-group/bifrost-go/executionprofile"
)

func deliveryFixture(t *testing.T) ([]byte, executionprofile.Decoded, time.Time) {
	t.Helper()
	// Certificate generation needs no server or network socket.
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	certificate := &x509.Certificate{SerialNumber: big.NewInt(1), IsCA: true, BasicConstraintsValid: true,
		NotBefore: time.Date(2026, 10, 10, 0, 0, 0, 0, time.UTC), NotAfter: time.Date(2026, 10, 11, 0, 0, 0, 0, time.UTC), KeyUsage: x509.KeyUsageCertSign}
	cert, err := x509.CreateCertificate(rand.Reader, certificate, certificate, public, private)
	if err != nil {
		t.Fatal(err)
	}
	id := "00000000-0000-0000-0000-000000000001"
	binding := map[string]any{
		"kind": "execution-binding/v1", "execution_kind": "workflow",
		"execution_id": id, "attempt_id": id, "attempt_number": 1,
		"solution_id": id, "deployment_id": id, "artifact_id": "sha256:" + strings.Repeat("a", 64),
		"session_id": id, "supervisor_incarnation_id": id, "runtime_incarnation_id": id,
		"original_caller": map[string]any{"caller_id": id, "organization_id": id},
		"effective_scope": map[string]any{"kind": "organization", "organization_id": id},
	}
	frame := executionprofile.Frame{
		"protocol": executionprofile.Protocol, "type": "Provision", "session_id": id,
		"message_id": id, "sequence": 1, "correlation_id": id,
		"body": map[string]any{"binding": binding, "prepare_message_id": id, "committed_start_id": id,
			"grant_id": id, "delivery_id": id, "expires_at": "2026-10-10T20:00:00Z",
			"capabilities": []string{"integration-get"}, "operations_digest": "sha256:" + strings.Repeat("b", 64)},
	}
	raw, err := json.Marshal(frame)
	if err != nil {
		t.Fatal(err)
	}
	expected, err := executionprofile.Decode(raw)
	if err != nil {
		t.Fatal(err)
	}
	sdk := SDKConfiguration{Endpoint: "https://sdk.fixture.invalid", Bearer: "private-test-bearer",
		OrganizationID: id, SolutionID: id, TestCAPEM: string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: cert}))}
	envelope, err := json.Marshal(map[string]any{"version": "runtime-private-delivery/v1", "provision": json.RawMessage(raw), "sdk_configuration": sdk})
	if err != nil {
		t.Fatal(err)
	}
	return envelope, expected, time.Date(2026, 10, 10, 19, 0, 0, 0, time.UTC)
}

func deliveryWire(raw []byte) []byte {
	wire := make([]byte, 4+len(raw))
	binary.BigEndian.PutUint32(wire, uint32(len(raw)))
	copy(wire[4:], raw)
	return wire
}

func TestOnePrivateDelivery(t *testing.T) {
	raw, expected, now := deliveryFixture(t)
	r := &DeliveryReader{}
	sdk, err := r.Read(bytes.NewReader(deliveryWire(raw)), expected, now)
	if err != nil || sdk.Bearer != "private-test-bearer" {
		t.Fatal("valid delivery rejected")
	}
	if _, err := r.Read(bytes.NewReader(deliveryWire(raw)), expected, now); err != DeliveryRejected {
		t.Fatal("delivery replay accepted")
	}
}

func TestMalformedPrivateDelivery(t *testing.T) {
	raw, expected, now := deliveryFixture(t)
	wire := deliveryWire(raw)
	oversize := make([]byte, 4)
	binary.BigEndian.PutUint32(oversize, MaxDeliveryBytes+1)
	for name, input := range map[string][]byte{
		"absent": nil, "short-prefix": wire[:3], "truncated": wire[:len(wire)-1],
		"oversized": oversize, "empty": make([]byte, 4), "second-delivery": append(bytes.Clone(wire), wire...),
		"duplicate":         deliveryWire(bytes.Replace(raw, []byte(`"version":`), []byte(`"version":"runtime-private-delivery/v1","version":`), 1)),
		"escaped-duplicate": deliveryWire(bytes.Replace(raw, []byte(`"version":`), []byte(`"\u0076ersion":"runtime-private-delivery/v1","version":`), 1)),
		"noncanonical-key":  deliveryWire(bytes.Replace(raw, []byte(`"version":`), []byte(`"VERSION":`), 1)),
		"nested-duplicate":  deliveryWire(bytes.Replace(raw, []byte(`"bearer":`), []byte(`"bearer":"other","bearer":`), 1)),
		"unknown-sdk-field": deliveryWire(bytes.Replace(raw, []byte(`"bearer":`), []byte(`"authority":"admin","bearer":`), 1)),
		"unknown-field":     deliveryWire(bytes.Replace(raw, []byte(`"version":`), []byte(`"authority":true,"version":`), 1)),
		"wrong-scope":       deliveryWire(bytes.Replace(raw, []byte(`"organization_id":"00000000-0000-0000-0000-000000000001","solution_id"`), []byte(`"organization_id":"00000000-0000-0000-0000-000000000002","solution_id"`), 1)),
		"wrong-origin":      deliveryWire(bytes.Replace(raw, []byte("https://sdk.fixture.invalid"), []byte("http://sdk.fixture.invalid"), 1)),
		"header-control":    deliveryWire(bytes.Replace(raw, []byte("private-test-bearer"), []byte(`private\ttest`), 1)),
		"invalid-unicode":   deliveryWire(bytes.Replace(raw, []byte("private-test-bearer"), []byte(`\ud800`), 1)),
		"invalid-utf8":      deliveryWire(bytes.Replace(raw, []byte("private-test-bearer"), []byte{0xff}, 1)),
		"trailing-json":     deliveryWire(append(bytes.Clone(raw), []byte("{}")...)),
	} {
		t.Run(name, func(t *testing.T) {
			r := &DeliveryReader{}
			if _, err := r.Read(bytes.NewReader(input), expected, now); err != DeliveryRejected {
				t.Fatal("invalid delivery accepted")
			}
			if _, err := r.Read(bytes.NewReader(wire), expected, now); err != DeliveryRejected {
				t.Fatal("failed delivery reused")
			}
		})
	}
}

func TestExpiredOrMismatchingProvision(t *testing.T) {
	raw, expected, now := deliveryFixture(t)
	t.Run("expired", func(t *testing.T) {
		if _, err := (&DeliveryReader{}).Read(bytes.NewReader(deliveryWire(raw)), expected, now.Add(time.Hour)); err != DeliveryRejected {
			t.Fatal("expired delivery accepted")
		}
	})
	t.Run("different-message", func(t *testing.T) {
		expected.Frame["message_id"] = "00000000-0000-0000-0000-000000000002"
		if _, err := (&DeliveryReader{}).Read(bytes.NewReader(deliveryWire(raw)), expected, now); err != DeliveryRejected {
			t.Fatal("mismatching delivery accepted")
		}
	})
}

func TestStrictPrivateJSON(t *testing.T) {
	for _, raw := range []string{`{"x":{"a":1,"a":2}}`, `{"x":"\udc00"}`, `{"x":"\ud800x"}`, strings.Repeat("[", 18) + "0" + strings.Repeat("]", 18)} {
		if strictJSON([]byte(raw)) {
			t.Fatal("ambiguous JSON accepted")
		}
	}
	if !strictJSON([]byte(`{"x":"\ud83d\ude00"}`)) {
		t.Fatal("valid surrogate pair rejected")
	}
}

type noEOFReader struct{ io.Reader }

func (r noEOFReader) Read(p []byte) (int, error) {
	n, err := r.Reader.Read(p)
	if err == io.EOF {
		return 0, nil
	}
	return n, err
}

func TestDeliveryRequiresClosedWriter(t *testing.T) {
	raw, expected, now := deliveryFixture(t)
	if _, err := (&DeliveryReader{}).Read(noEOFReader{bytes.NewReader(deliveryWire(raw))}, expected, now); err != DeliveryRejected {
		t.Fatal("writer not closed")
	}
}
