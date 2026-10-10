//go:build linux && amd64

package nativeadapter

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"io"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/midtown-technology-group/bifrost-go/executionprofile"
)

// This uses the test executable as synthetic inert ELF input, never as an
// accepted deployment or workload. No subprocess is created by these tests.
func preparationFixture(t *testing.T) (executionprofile.Decoded, PreparationInputs, time.Time) {
	t.Helper()
	raw, err := os.ReadFile("../../executionprofile/testdata/structural-vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var vectors []struct {
		Name  string          `json:"name"`
		Frame json.RawMessage `json:"frame"`
	}
	if err := json.Unmarshal(raw, &vectors); err != nil {
		t.Fatal(err)
	}
	var decoded executionprofile.Decoded
	for _, vector := range vectors {
		if vector.Name == "valid-Prepare" {
			decoded, err = executionprofile.Decode(vector.Frame)
			if err != nil {
				t.Fatal(err)
			}
		}
	}
	if decoded.Frame == nil {
		t.Fatal("missing independent Prepare vector")
	}
	path, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	child, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	adapter := []byte("synthetic adapter bytes, not an accepted bundle")
	body := decoded.Frame["body"].(map[string]any)
	artifact := body["artifact"].(map[string]any)
	artifact["executable_sha256"] = bytesDigest(child)
	artifact["adapter_sha256"] = bytesDigest(adapter)
	artifact["sdk"] = map[string]any{"distribution": "github.com/midtown-technology-group/bifrost-go", "version": "0.0.0-spike.2"}
	inputSchema := []byte(`{"type":"object","required":["name"],"properties":{"name":{"type":"string","minLength":1}},"additionalProperties":false}`)
	outputSchema := []byte(`{"type":"object"}`)
	workload := body["workload"].(map[string]any)
	workload["input_schema_digest"] = "sha256:" + bytesDigest(inputSchema)
	workload["output_schema_digest"] = "sha256:" + bytesDigest(outputSchema)
	workload["input"] = map[string]any{"name": "fixture"}
	accepted := PreparationInputs{Binding: cloneProtocolValue(body["binding"]).(map[string]any), Artifact: cloneProtocolValue(artifact).(map[string]any), Child: bytes.NewReader(child), Adapter: bytes.NewReader(adapter), InputSchema: inputSchema, OutputSchema: outputSchema}
	return decoded, accepted, time.Date(2030, 1, 1, 0, 0, 0, 0, time.UTC)
}

func TestPreparationIsInertAndDetached(t *testing.T) {
	decoded, accepted, now := preparationFixture(t)
	prepared, err := PrepareNative(decoded, accepted, now)
	if err != nil {
		t.Fatal(err)
	}
	defer prepared.Close()
	body := prepared.PreparedBody()
	if !reflect.DeepEqual(body["artifact"], accepted.Artifact) {
		t.Fatal("Prepared artifact drift")
	}
	body["artifact"].(map[string]any)["sdk"].(map[string]any)["version"] = "mutated"
	decoded.Frame["body"].(map[string]any)["artifact"].(map[string]any)["artifact_id"] = "mutated"
	if !reflect.DeepEqual(prepared.PreparedBody()["artifact"], accepted.Artifact) {
		t.Fatal("caller mutated retained preparation")
	}
	if string(prepared.input) != `{"name":"fixture"}` || !prepared.deadline.After(now) {
		t.Fatal("input or deadline drift")
	}
	if err := prepared.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := prepared.executable.file.Stat(); err == nil {
		t.Fatal("sealed handle survived close")
	}
}

func TestPreparationRejectsDriftBeforeLaunch(t *testing.T) {
	for _, name := range []string{"binding", "accepted-artifact", "session", "caller", "context", "deadline", "no-deadline", "input-schema", "output-schema", "unsupported-schema", "duplicate-schema", "input", "child-digest", "adapter-digest", "non-elf", "elf-machine", "elf-endian", "elf-loader", "platform", "sdk", "missing-child", "mutable-frame"} {
		t.Run(name, func(t *testing.T) {
			decoded, accepted, now := preparationFixture(t)
			body := decoded.Frame["body"].(map[string]any)
			workload := body["workload"].(map[string]any)
			artifact := body["artifact"].(map[string]any)
			switch name {
			case "binding":
				accepted.Binding["attempt_id"] = "00000000-0000-0000-0000-000000000099"
			case "accepted-artifact":
				accepted.Artifact["build_evidence_sha256"] = strings.Repeat("f", 64)
			case "session":
				decoded.Frame["session_id"] = "00000000-0000-0000-0000-000000000099"
			case "caller":
				body["context"].(map[string]any)["caller_id"] = "00000000-0000-0000-0000-000000000099"
			case "context":
				body["context"].(map[string]any)["attempt_number"] = json.Number("2")
			case "deadline":
				now = now.Add(time.Minute)
			case "no-deadline":
				workload["deadline_utc"] = nil
			case "input-schema":
				accepted.InputSchema = []byte(`{}`)
			case "output-schema":
				accepted.OutputSchema = []byte(`{}`)
			case "unsupported-schema", "duplicate-schema":
				accepted.InputSchema = []byte(`{"type":"object","pattern":"unsupported"}`)
				if name == "duplicate-schema" {
					accepted.InputSchema = []byte(`{"type":"object","type":"object"}`)
				}
				workload["input_schema_digest"] = "sha256:" + bytesDigest(accepted.InputSchema)
			case "input":
				workload["input"] = map[string]any{"name": ""}
			case "child-digest":
				accepted.Child = strings.NewReader("altered")
			case "adapter-digest":
				accepted.Adapter = strings.NewReader("altered")
			case "non-elf":
				child := []byte("not executable")
				artifact["executable_sha256"] = bytesDigest(child)
				accepted.Artifact = cloneProtocolValue(artifact).(map[string]any)
				accepted.Child = bytes.NewReader(child)
			case "elf-machine", "elf-endian", "elf-loader":
				child, err := io.ReadAll(accepted.Child)
				if err != nil || len(child) < 64 {
					t.Fatal("invalid synthetic test ELF")
				}
				switch name {
				case "elf-machine":
					binary.LittleEndian.PutUint16(child[18:20], 183) // ARM64.
				case "elf-endian":
					child[5] = 2
				case "elf-loader":
					offset := binary.LittleEndian.Uint64(child[32:40])
					if offset > uint64(len(child)-4) {
						t.Fatal("invalid test program header")
					}
					binary.LittleEndian.PutUint32(child[offset:offset+4], 3) // PT_INTERP.
				}
				// Update both synthetic references so ELF validation, not digest
				// mismatch, has to reject the unsupported executable shape.
				artifact["executable_sha256"] = bytesDigest(child)
				accepted.Artifact = cloneProtocolValue(artifact).(map[string]any)
				accepted.Child = bytes.NewReader(child)
			case "platform":
				artifact["platform"].(map[string]any)["architecture"] = "arm64"
				accepted.Artifact = cloneProtocolValue(artifact).(map[string]any)
			case "sdk":
				artifact["sdk"].(map[string]any)["version"] = "future"
				accepted.Artifact = cloneProtocolValue(artifact).(map[string]any)
			case "missing-child":
				accepted.Child = nil
			case "mutable-frame":
				body["artifact"] = nil
			}
			prepared, err := PrepareNative(decoded, accepted, now)
			if prepared != nil {
				_ = prepared.Close()
				t.Fatal("invalid preparation retained bytes")
			}
			if err != PreparationRejected {
				t.Fatal("invalid preparation accepted")
			}
		})
	}
}

func TestWorkloadSchemaParsingIsStrictAndBounded(t *testing.T) {
	for _, raw := range []string{`{"type":"string","type":"number"}`, `{"type":"string"}{}`, `{"type":"string","minLength":1e999999999}`, `{"type":"string","minLength":1e65537}`, `{"type":"string","title":"unsupported"}`} {
		if _, ok := parseWorkloadSchema([]byte(raw)); ok {
			t.Fatal("invalid schema accepted")
		}
	}
	raw := `{"type":"string"}`
	for index := 0; index < 20; index++ {
		raw = `{"type":"array","items":` + raw + `}`
	}
	if _, ok := parseWorkloadSchema([]byte(raw)); !ok {
		t.Fatal("workload incorrectly limited to private delivery depth")
	}
}
