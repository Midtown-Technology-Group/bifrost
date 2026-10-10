//go:build linux && amd64

package nativeadapter

import (
	"bytes"
	"crypto/sha256"
	"debug/elf"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"reflect"
	"time"

	"github.com/midtown-technology-group/bifrost-go/executionprofile"
)

var PreparationRejected = errors.New("native preparation rejected")

// PreparationInputs must be supplied from the guardian's accepted immutable
// bundle. These references are not authenticated by passing this struct.
// The adapter independently checks deployed bytes; Rust retains admission.
type PreparationInputs struct {
	Binding      map[string]any
	Artifact     map[string]any
	Child        io.Reader
	Adapter      io.Reader
	InputSchema  []byte
	OutputSchema []byte
}

// PreparedNative owns inert sealed child bytes. It contains no SDK material and
// exposes no launch method. Preparation never evaluates tenant code.
type PreparedNative struct {
	executable     *SealedExecutable
	prepareID      string
	artifact       map[string]any
	input          []byte
	outputSchema   []byte
	deadline       time.Time
	binding        map[string]any
	sessionID      string
	parentSequence int64
}

func (p *PreparedNative) Close() error { return p.executable.Close() }

// PreparedBody returns a detached common-protocol value, never Rust wire types.
func (p *PreparedNative) PreparedBody() map[string]any {
	return map[string]any{"prepare_message_id": p.prepareID, "artifact": cloneProtocolValue(p.artifact)}
}

func cloneProtocolValue(value any) any {
	switch value := value.(type) {
	case map[string]any:
		copy := make(map[string]any, len(value))
		for key, child := range value {
			copy[key] = cloneProtocolValue(child)
		}
		return copy
	case []any:
		copy := make([]any, len(value))
		for index, child := range value {
			copy[index] = cloneProtocolValue(child)
		}
		return copy
	default:
		return value
	}
}

func bytesDigest(raw []byte) string { sum := sha256.Sum256(raw); return hex.EncodeToString(sum[:]) }

func parseWorkloadSchema(raw []byte) (any, bool) {
	if len(raw) == 0 || len(raw) > 65536 || !strictJSONDepth(raw, 64) {
		return nil, false
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var schema any
	if d.Decode(&schema) != nil || !supportedWorkload(schema, 0) {
		return nil, false
	}
	return schema, true
}

func PrepareNative(received executionprofile.Decoded, accepted PreparationInputs, now time.Time) (*PreparedNative, error) {
	// Decoded.Frame is a mutable public view. Revalidate and detach it before any
	// type assertion or retaining state; this does not replace parent raw evidence.
	raw, err := json.Marshal(received.Frame)
	if err != nil {
		return nil, PreparationRejected
	}
	validated, err := executionprofile.Decode(raw)
	if err != nil || validated.Frame["type"] != "Prepare" || accepted.Child == nil || accepted.Adapter == nil {
		return nil, PreparationRejected
	}
	body := validated.Frame["body"].(map[string]any)
	binding := body["binding"].(map[string]any)
	artifact := body["artifact"].(map[string]any)
	if !reflect.DeepEqual(binding, accepted.Binding) || !reflect.DeepEqual(artifact, accepted.Artifact) || binding["execution_kind"] != "workflow" || binding["session_id"] != validated.Frame["session_id"] || binding["artifact_id"] != artifact["artifact_id"] || artifact["kind"] != "native-executable/v1" {
		return nil, PreparationRejected
	}
	platform := artifact["platform"].(map[string]any)
	sdk := artifact["sdk"].(map[string]any)
	if platform["os"] != "linux" || platform["architecture"] != "amd64" || sdk["distribution"] != "github.com/midtown-technology-group/bifrost-go" || sdk["version"] != "0.0.0-spike.2" {
		return nil, PreparationRejected
	}
	context := body["context"].(map[string]any)
	caller := binding["original_caller"].(map[string]any)
	if context["caller_id"] != caller["caller_id"] {
		return nil, PreparationRejected
	}
	for _, name := range []string{"execution_kind", "execution_id", "attempt_id", "attempt_number", "solution_id", "deployment_id", "artifact_id", "effective_scope"} {
		if !reflect.DeepEqual(context[name], binding[name]) {
			return nil, PreparationRejected
		}
	}
	workload := body["workload"].(map[string]any)
	deadlineText, ok := workload["deadline_utc"].(string)
	if !ok {
		return nil, PreparationRejected
	} // No no-timeout/renewal release in this slice.
	deadline, err := time.Parse(time.RFC3339Nano, deadlineText)
	if err != nil || !deadline.After(now) {
		return nil, PreparationRejected
	}
	if workload["input_schema_digest"] != "sha256:"+bytesDigest(accepted.InputSchema) || workload["output_schema_digest"] != "sha256:"+bytesDigest(accepted.OutputSchema) {
		return nil, PreparationRejected
	}
	inputSchema, ok := parseWorkloadSchema(accepted.InputSchema)
	if !ok {
		return nil, PreparationRejected
	}
	if _, ok := parseWorkloadSchema(accepted.OutputSchema); !ok || !workloadMatches(inputSchema, workload["input"]) {
		return nil, PreparationRejected
	}
	input, err := json.Marshal(workload["input"])
	if err != nil || len(input) > 65536 {
		return nil, PreparationRejected
	}
	// Verify the adapter independently of the tenant. Neither reader is executed.
	hash := sha256.New()
	size, err := io.Copy(hash, io.LimitReader(accepted.Adapter, MaxExecutableBytes+1))
	if err != nil || size == 0 || size > MaxExecutableBytes || hex.EncodeToString(hash.Sum(nil)) != artifact["adapter_sha256"] {
		return nil, PreparationRejected
	}
	executable, err := SealExecutable(accepted.Child, artifact["executable_sha256"].(string))
	if err != nil {
		return nil, PreparationRejected
	}
	keep := false
	defer func() {
		if !keep {
			_ = executable.Close()
		}
	}()
	format, err := elf.NewFile(executable.file)
	if err != nil || format.Class != elf.ELFCLASS64 || format.Data != elf.ELFDATA2LSB || format.Machine != elf.EM_X86_64 || (format.Type != elf.ET_EXEC && format.Type != elf.ET_DYN) {
		return nil, PreparationRejected
	}
	executableSegment := false
	for _, segment := range format.Progs {
		// The initial CGO-disabled bundle requires no ambient dynamic loader.
		if segment.Type == elf.PT_INTERP {
			return nil, PreparationRejected
		}
		if segment.Type == elf.PT_LOAD && segment.Flags&elf.PF_X != 0 && format.Entry >= segment.Vaddr && format.Entry-segment.Vaddr < segment.Memsz {
			executableSegment = true
		}
	}
	if !executableSegment {
		return nil, PreparationRejected
	}
	sequence, err := validated.Frame["sequence"].(json.Number).Int64()
	if err != nil {
		return nil, PreparationRejected
	}
	keep = true
	return &PreparedNative{executable: executable, prepareID: validated.Frame["message_id"].(string), artifact: artifact, input: input, outputSchema: append([]byte(nil), accepted.OutputSchema...), deadline: deadline, binding: binding, sessionID: validated.Frame["session_id"].(string), parentSequence: sequence}, nil
}
