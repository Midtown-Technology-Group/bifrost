// Package nativeadapter contains trusted adapter components, never tenant code.
// These components do not issue credentials, admit executions or authorize spawn.
package nativeadapter

import (
	"bytes"
	"crypto/x509"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"reflect"
	"strconv"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/midtown-technology-group/bifrost-go/executionprofile"
)

const MaxDeliveryBytes = 64 * 1024

// DeliveryRejected is deliberately static: parsing errors must not echo secrets.
var DeliveryRejected = errors.New("private runtime delivery rejected")

// SDKConfiguration is the only material permitted on the separate tenant FD3.
// It carries no control-plane identity, delivery reference or lifecycle authority.
type SDKConfiguration struct {
	Endpoint       string `json:"endpoint"`
	Bearer         string `json:"bearer"`
	OrganizationID string `json:"organization_id"`
	SolutionID     string `json:"solution_id"`
	TestCAPEM      string `json:"test_ca_pem"`
}

// DeliveryReader consumes one private delivery even when validation fails.
// The guardian must own the sole writer of the inherited pipe and close it after
// writing. This parser does not establish custody: passing an arbitrary Reader
// or matching a Provision is never evidence of issuer or launch authority.
type DeliveryReader struct {
	mu   sync.Mutex
	used bool
}

func (r *DeliveryReader) Read(reader io.Reader, expected executionprofile.Decoded, now time.Time) (SDKConfiguration, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.used {
		return SDKConfiguration{}, DeliveryRejected
	}
	r.used = true
	var prefix [4]byte
	if _, err := io.ReadFull(reader, prefix[:]); err != nil {
		return SDKConfiguration{}, DeliveryRejected
	}
	size := binary.BigEndian.Uint32(prefix[:])
	if size == 0 || size > MaxDeliveryBytes {
		return SDKConfiguration{}, DeliveryRejected
	}
	raw := make([]byte, int(size))
	if _, err := io.ReadFull(reader, raw); err != nil {
		return SDKConfiguration{}, DeliveryRejected
	}
	// EOF is part of the one-delivery contract. The owning adapter must close the
	// descriptor on cancellation/deadline so a stalled writer cannot hold launch.
	var extra [1]byte
	if n, err := reader.Read(extra[:]); n != 0 || err != io.EOF {
		return SDKConfiguration{}, DeliveryRejected
	}
	return decodeDelivery(raw, expected, now)
}

func decodeDelivery(raw []byte, expected executionprofile.Decoded, now time.Time) (SDKConfiguration, error) {
	if !strictJSON(raw) || !closedObject(raw, "version", "provision", "sdk_configuration") {
		return SDKConfiguration{}, DeliveryRejected
	}
	var envelope struct {
		Version   string           `json:"version"`
		Provision json.RawMessage  `json:"provision"`
		SDK       SDKConfiguration `json:"sdk_configuration"`
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&envelope) != nil || envelope.Version != "runtime-private-delivery/v1" {
		return SDKConfiguration{}, DeliveryRejected
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || !closedObject(fields["sdk_configuration"], "endpoint", "bearer", "organization_id", "solution_id", "test_ca_pem") {
		return SDKConfiguration{}, DeliveryRejected
	}
	decoded, err := executionprofile.Decode(envelope.Provision)
	if err != nil || decoded.Frame["type"] != "Provision" || !reflect.DeepEqual(decoded.Frame, expected.Frame) {
		return SDKConfiguration{}, DeliveryRejected
	}
	body := decoded.Frame["body"].(map[string]any)
	binding := body["binding"].(map[string]any)
	scope := binding["effective_scope"].(map[string]any)
	capabilities := body["capabilities"].([]any)
	expires, err := time.Parse(time.RFC3339Nano, body["expires_at"].(string))
	if err != nil || !expires.After(now) || binding["execution_kind"] != "workflow" || binding["session_id"] != decoded.Frame["session_id"] || scope["kind"] != "organization" || scope["organization_id"] != envelope.SDK.OrganizationID || binding["solution_id"] != envelope.SDK.SolutionID || len(capabilities) != 1 || capabilities[0] != "integration-get" {
		return SDKConfiguration{}, DeliveryRejected
	}
	sdk := envelope.SDK
	origin, err := url.Parse(sdk.Endpoint)
	if err != nil || origin.Scheme != "https" || origin.Host == "" || origin.User != nil || origin.RawQuery != "" || origin.ForceQuery || origin.Fragment != "" || (origin.Path != "" && origin.Path != "/") || !validBearer(sdk.Bearer) || !x509.NewCertPool().AppendCertsFromPEM([]byte(sdk.TestCAPEM)) {
		return SDKConfiguration{}, DeliveryRejected
	}
	return sdk, nil
}

func validBearer(value string) bool {
	if value == "" {
		return false
	}
	for _, b := range []byte(value) {
		if b < 33 || b > 126 {
			return false
		}
	}
	return true
}

func closedObject(raw []byte, names ...string) bool {
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || len(fields) != len(names) {
		return false
	}
	for _, name := range names {
		if _, ok := fields[name]; !ok {
			return false
		}
	}
	return true
}

// strictJSON rejects duplicate decoded keys at every depth, malformed UTF-8 and
// invalid Unicode escapes before encoding/json can normalize them. Depth is
// bounded independently of the larger public protocol limit.
func strictJSON(raw []byte) bool {
	if !utf8.Valid(raw) || !validUnicodeEscapes(raw) {
		return false
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	if !walkJSON(d, 0) {
		return false
	}
	_, err := d.Token()
	return err == io.EOF
}

func walkJSON(d *json.Decoder, depth int) bool {
	if depth > 16 {
		return false
	}
	token, err := d.Token()
	if err != nil {
		return false
	}
	delim, composite := token.(json.Delim)
	if !composite {
		return true
	}
	if delim != '{' && delim != '[' {
		return false
	}
	keys := map[string]bool{}
	for d.More() {
		if delim == '{' {
			key, err := d.Token()
			name, ok := key.(string)
			if err != nil || !ok || keys[name] {
				return false
			}
			keys[name] = true
		}
		if !walkJSON(d, depth+1) {
			return false
		}
	}
	end, err := d.Token()
	return err == nil && ((delim == '{' && end == json.Delim('}')) || (delim == '[' && end == json.Delim(']')))
}

func validUnicodeEscapes(raw []byte) bool {
	quoted := false
	for i := 0; i < len(raw); i++ {
		if raw[i] == '"' {
			quoted = !quoted
			continue
		}
		if !quoted || raw[i] != '\\' {
			continue
		}
		i++
		if i >= len(raw) {
			return false
		}
		if raw[i] != 'u' {
			continue
		}
		if i+4 >= len(raw) {
			return false
		}
		n, err := strconv.ParseUint(string(raw[i+1:i+5]), 16, 16)
		if err != nil {
			return false
		}
		i += 4
		if n >= 0xdc00 && n <= 0xdfff {
			return false
		}
		if n >= 0xd800 && n <= 0xdbff {
			if i+6 >= len(raw) || raw[i+1] != '\\' || raw[i+2] != 'u' {
				return false
			}
			low, err := strconv.ParseUint(string(raw[i+3:i+7]), 16, 16)
			if err != nil || low < 0xdc00 || low > 0xdfff {
				return false
			}
			i += 6
		}
	}
	return true
}
