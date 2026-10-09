package executionprofile

// These are specification tests only. No model below executes a workload,
// issues a grant or commits a database transaction. Authority events are injected
// fixture assumptions, not evidence of Rust/supervisor custody or finalization.
import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"testing"
	"time"
)

func fixtureJSON(t *testing.T, path string, out any) {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(out); err != nil {
		t.Fatal(err)
	}
}
func TestPublishedStructuralDocuments(t *testing.T) {
	var vectors []struct {
		Name  string
		Frame any
		Valid bool
	}
	fixtureJSON(t, "testdata/structural-vectors.json", &vectors)
	if len(vectors) != 68 {
		t.Fatal("changed structural inventory")
	}
	for _, vector := range vectors {
		t.Run(vector.Name, func(t *testing.T) {
			root := registry[profileID]
			if valid(root, vector.Frame, root) != vector.Valid {
				t.Fatal("published structural expectation differs")
			}
		})
	}
}

type specModel struct {
	binding, artifact                                                         map[string]any
	frames                                                                    map[string]Frame
	seq                                                                       map[string]int64
	ids                                                                       map[string][]byte
	committedStart, budget, winner                                            any
	grant, receipt                                                            map[string]any
	resultHash                                                                string
	observedStart, observedCancel, material, released                         bool
	reportedStart, reportedCancel, effects, stopped, closed, revoked, cleanup bool
	elapsed, commitElapsed, heartbeat, logSequence                            int64
	now                                                                       time.Time
}

func newSpecModel(binding, artifact map[string]any) *specModel {
	return &specModel{binding: binding, artifact: artifact, frames: map[string]Frame{},
		seq: map[string]int64{"parent": 0, "adapter": 0}, ids: map[string][]byte{},
		now: time.Date(2030, 1, 1, 0, 0, 0, 0, time.UTC)}
}
func (s *specModel) copy() *specModel {
	c := *s
	c.frames = map[string]Frame{}
	for k, v := range s.frames {
		c.frames[k] = v
	}
	c.seq = map[string]int64{}
	for k, v := range s.seq {
		c.seq[k] = v
	}
	c.ids = map[string][]byte{}
	for k, v := range s.ids {
		c.ids[k] = v
	}
	return &c
}
func (s *specModel) snapshot() map[string]any {
	var disposition any
	if s.receipt != nil {
		disposition = s.receipt["disposition"]
	}
	return map[string]any{"effects_permitted": s.effects, "winner": s.winner, "closed": s.closed,
		"cleanup_verified": s.cleanup, "result_observed": s.frames["Result"] != nil,
		"receipt": disposition, "sequences": s.seq}
}
func specNumber(value any) int64 { n, _ := value.(json.Number).Int64(); return n }
func specTime(value any) time.Time {
	s, _ := value.(string)
	n, _ := time.Parse(time.RFC3339Nano, s)
	return n
}
func contains(value any, expected string) bool {
	for _, v := range value.([]any) {
		if v == expected {
			return true
		}
	}
	return false
}
func tenantProjection(binding map[string]any) map[string]any {
	result := map[string]any{"kind": "tenant-context/v1"}
	for _, key := range []string{"execution_kind", "execution_id", "attempt_id", "attempt_number", "solution_id", "deployment_id", "artifact_id", "effective_scope"} {
		result[key] = binding[key]
	}
	result["caller_id"] = binding["original_caller"].(map[string]any)["caller_id"]
	return result
}
func (s *specModel) messageID(kind string) any {
	if f := s.frames[kind]; f != nil {
		return f["message_id"]
	}
	return nil
}
func (s *specModel) receive(direction string, decoded Decoded) error {
	c := s.copy()
	if err := c.receiveCandidate(direction, decoded); err != nil {
		return err
	}
	*s = *c
	return nil
}
func (s *specModel) receiveCandidate(direction string, decoded Decoded) error {
	f := decoded.Frame
	if s.closed || direction == "adapter" && s.stopped {
		return Code("SessionClosed")
	}
	if f["session_id"] != s.binding["session_id"] {
		return Code("InvalidBinding")
	}
	sequence, known := s.seq[direction]
	if !known || specNumber(f["sequence"]) != sequence+1 {
		return Code("InvalidTransition")
	}
	id := f["message_id"].(string)
	if old, seen := s.ids[id]; seen {
		if bytes.Equal(old, decoded.payload) {
			return Code("DuplicateMessage")
		}
		return Code("ConflictingMessage")
	}
	kind := f["type"].(string)
	body := f["body"].(map[string]any)
	parent := kind == "Select" || kind == "Prepare" || kind == "Start" || kind == "Provision" || kind == "Cancel" || kind == "ResultReceipt"
	if parent != (direction == "parent") {
		return Code("InvalidTransition")
	}
	var expected any
	switch kind {
	case "Offer":
		if s.frames[kind] != nil {
			return Code("InvalidTransition")
		}
		if !contains(body["supported_protocols"], Protocol) {
			return UnsupportedProtocol
		}
		if !contains(body["capabilities"], "execution_profile/v1") {
			return Code("UnsupportedProfile")
		}
		if body["runtime_incarnation_id"] != s.binding["runtime_incarnation_id"] {
			return Code("InvalidBinding")
		}
	case "Select":
		if s.frames["Offer"] == nil || s.frames[kind] != nil {
			return Code("InvalidTransition")
		}
		expected = s.messageID("Offer")
		if !contains(s.frames["Offer"]["body"].(map[string]any)["artifact_classes"], body["artifact_class"].(string)) {
			return Code("UnsupportedProfile")
		}
		if body["artifact_class"] != s.artifact["kind"] {
			return Code("InvalidBinding")
		}
	case "Prepare":
		if s.frames["Select"] == nil || s.frames[kind] != nil || s.winner != nil {
			return Code("InvalidTransition")
		}
		expected = s.messageID("Select")
		if !reflect.DeepEqual(body["binding"], s.binding) || !reflect.DeepEqual(body["artifact"], s.artifact) || !reflect.DeepEqual(body["context"], tenantProjection(s.binding)) {
			return Code("InvalidBinding")
		}
	case "Prepared":
		if s.frames["Prepare"] == nil || s.frames[kind] != nil || s.winner != nil {
			return Code("InvalidTransition")
		}
		expected = s.messageID("Prepare")
		if body["prepare_message_id"] != expected || !reflect.DeepEqual(body["artifact"], s.artifact) {
			return Code("InvalidBinding")
		}
	case "Start":
		if s.frames["Prepared"] == nil || s.frames[kind] != nil || s.winner != nil {
			return Code("InvalidTransition")
		}
		expected = s.messageID("Prepare")
		if body["prepare_message_id"] != expected || body["committed_start_id"] != s.committedStart || body["remaining_run_ms"] != s.budget {
			return Code("InvalidBinding")
		}
	case "Provision":
		if s.frames["Prepared"] == nil || s.frames[kind] != nil || s.winner != nil || s.committedStart == nil {
			return Code("InvalidTransition")
		}
		expected = s.messageID("Prepare")
		if !reflect.DeepEqual(body, s.grant) || !reflect.DeepEqual(body["binding"], s.binding) || body["committed_start_id"] != s.committedStart {
			return Code("InvalidGrant")
		}
	case "Cancel":
		if s.frames["Select"] == nil || s.frames[kind] != nil || (s.winner != "cancel" && s.winner != "failure" && s.winner != "result") {
			return Code("InvalidTransition")
		}
	case "ResultReceipt":
		if s.frames["Result"] == nil || !reflect.DeepEqual(body, s.receipt) {
			return Code("InvalidTransition")
		}
		expected = s.messageID("Result")
	case "Result", "LogBatch", "Usage":
		if s.frames["Start"] == nil {
			return Code("InvalidTransition")
		}
		expected = s.messageID("Start")
		if body["start_message_id"] != expected {
			return Code("InvalidBinding")
		}
		if !s.released || s.frames["Result"] != nil {
			return Code("InvalidTransition")
		}
		if kind == "Result" {
			s.resultHash = decoded.PayloadSHA256()
		}
		if kind == "LogBatch" {
			if specNumber(body["batch_sequence"]) != s.logSequence+1 {
				return Code("InvalidTransition")
			}
			s.logSequence++
		}
		s.reportedStart = true
	case "Heartbeat":
		if s.frames["Prepared"] == nil || specNumber(body["monotonic_elapsed_ms"]) < s.heartbeat {
			return Code("InvalidTransition")
		}
		state := body["state"]
		matched := state == "prepared" && !s.reportedStart && !s.reportedCancel && body["start_message_id"] == nil
		if state == "cancelling" {
			matched = s.frames["Cancel"] != nil && body["start_message_id"] == s.messageID("Start")
		}
		if state == "executing" {
			matched = s.frames["Start"] != nil && body["start_message_id"] == s.messageID("Start") && s.released && !s.reportedCancel
		}
		if !matched {
			return Code("InvalidTransition")
		}
		s.heartbeat = specNumber(body["monotonic_elapsed_ms"])
		s.reportedStart = s.reportedStart || body["start_message_id"] != nil
		s.reportedCancel = s.reportedCancel || state == "cancelling"
	case "Stopped":
		if s.frames["Select"] == nil {
			return Code("InvalidTransition")
		}
		var cancelID any
		if c := s.frames["Cancel"]; c != nil {
			cancelID = c["body"].(map[string]any)["cancel_id"]
		}
		startOK := body["start_message_id"] == s.messageID("Start") || !s.reportedStart && body["start_message_id"] == nil && (body["reason"] == "prepare_rejected" || body["reason"] == "protocol_error")
		cancelOK := body["cancel_id"] == cancelID || !s.reportedCancel && body["cancel_id"] == nil
		if !startOK || !cancelOK || body["result_message_id"] != s.messageID("Result") {
			return Code("InvalidBinding")
		}
		if body["reason"] == "completed" && (s.frames["Result"] == nil || s.reportedCancel) || body["reason"] == "cancelled" && (s.frames["Cancel"] == nil || body["cancel_id"] == nil) || body["reason"] == "prepare_rejected" && body["start_message_id"] != nil {
			return Code("InvalidTransition")
		}
		s.stopped = true
	}
	if f["correlation_id"] != expected {
		return Code("InvalidBinding")
	}
	s.seq[direction]++
	s.ids[id] = bytes.Clone(decoded.payload)
	switch kind {
	case "Offer", "Select", "Prepare", "Prepared", "Start", "Provision", "Cancel", "Result":
		s.frames[kind] = f
	}
	return nil
}
func (s *specModel) expired() bool {
	p := s.frames["Prepare"]["body"].(map[string]any)["workload"].(map[string]any)
	return p["deadline_utc"] != nil && !s.now.Before(specTime(p["deadline_utc"])) || s.budget != nil && s.elapsed-s.commitElapsed >= specNumber(s.budget)
}
func (s *specModel) assumption(event map[string]any) error {
	c := s.copy()
	if err := c.assumeCandidate(event); err != nil {
		return err
	}
	*s = *c
	return nil
}
func (s *specModel) assumeCandidate(event map[string]any) error {
	switch event["event"] {
	case "commit_start":
		if s.frames["Prepared"] == nil || s.committedStart != nil || s.winner != nil || s.closed {
			return Code("InvalidTransition")
		}
		s.committedStart = event["committed_start_id"]
		s.budget = event["remaining_run_ms"]
		s.commitElapsed = s.elapsed
	case "admit_grant":
		if s.committedStart == nil || s.closed || s.winner != nil || s.grant != nil {
			return Code("InvalidGrant")
		}
		grant := event["provision"].(map[string]any)
		if !reflect.DeepEqual(grant["binding"], s.binding) || grant["committed_start_id"] != s.committedStart || grant["prepare_message_id"] != s.messageID("Prepare") {
			return Code("InvalidGrant")
		}
		s.grant = grant
	case "observe_start":
		if s.frames["Start"] == nil || s.observedStart {
			return Code("InvalidTransition")
		}
		s.observedStart = true
	case "deliver":
		if s.frames["Provision"] == nil || event["delivery_id"] != s.grant["delivery_id"] {
			return Code("InvalidGrant")
		}
		s.material = true
	case "release":
		if !s.observedStart || !s.material || s.closed || s.winner != nil || s.observedCancel {
			return Code("EffectsForbidden")
		}
		if s.released {
			return Code("InvalidTransition")
		}
		if s.revoked || !s.now.Before(specTime(s.grant["expires_at"])) {
			return Code("InvalidGrant")
		}
		if s.expired() {
			return Code("DeadlineExceeded")
		}
		s.effects = true
		s.released = true
	case "tick":
		elapsed, now := specNumber(event["elapsed_ms"]), specTime(event["now"])
		if elapsed < s.elapsed || now.Before(s.now) {
			return Code("InvalidTransition")
		}
		s.elapsed = elapsed
		s.now = now
		if s.released && (!s.now.Before(specTime(s.grant["expires_at"])) || s.expired()) {
			s.effects = false
		}
	case "cancel_commit", "failure_commit":
		if s.winner == nil {
			if event["event"] == "cancel_commit" {
				s.winner = "cancel"
			} else {
				s.winner = "failure"
			}
		}
		s.effects = false
	case "observe_cancel":
		if s.frames["Cancel"] == nil {
			return Code("InvalidTransition")
		}
		s.observedCancel = true
		s.effects = false
	case "revoke":
		s.revoked = true
		s.effects = false
	case "accept_result":
		if s.receipt != nil {
			if event["result_sha256"] != s.receipt["result_sha256"] {
				return Code("ConflictingReceipt")
			}
			return nil
		}
		if s.closed {
			return Code("SessionClosed")
		}
		if s.frames["Result"] == nil {
			return Code("InvalidTransition")
		}
		if event["result_sha256"] != s.resultHash {
			return Code("ConflictingReceipt")
		}
		if s.winner == nil && (s.revoked || !s.now.Before(specTime(s.grant["expires_at"]))) {
			return Code("InvalidGrant")
		}
		if s.winner == nil && s.expired() {
			return Code("DeadlineExceeded")
		}
		if s.winner == nil {
			s.winner = "result"
		}
		disposition := "retained"
		if s.winner == "result" {
			disposition = "accepted"
		}
		s.receipt = map[string]any{"result_message_id": s.messageID("Result"), "result_sha256": s.resultHash, "decision_id": event["decision_id"], "winner": s.winner, "disposition": disposition}
		s.effects = false
	case "transport_loss", "child_crash", "adapter_crash", "close":
		s.closed = true
		s.effects = false
	case "cleanup_verified":
		s.cleanup = true
	default:
		return Code("UnknownEvent")
	}
	return nil
}
func applySpecStep(s *specModel, raw json.RawMessage) error {
	var event map[string]any
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&event); err != nil {
		return err
	}
	if event["event"] != "receive" {
		return s.assumption(event)
	}
	var literal struct {
		Frame json.RawMessage
		Hex   string
	}
	if err := json.Unmarshal(raw, &literal); err != nil {
		return err
	}
	var payload []byte
	if literal.Hex != "" {
		framed, err := decodeSpecHex(literal.Hex)
		if err != nil {
			return err
		}
		decoded, err := Read(bytes.NewReader(framed))
		if err != nil {
			return err
		}
		return s.receive(event["direction"].(string), decoded)
	}
	var compact bytes.Buffer
	// Preserve the published fixture's literal field order for its raw receipt
	// preimage. A Go re-encoding would correctly produce a different byte digest.
	if err := json.Compact(&compact, literal.Frame); err != nil {
		return err
	}
	payload = compact.Bytes()
	decoded, err := Decode(payload)
	if err != nil {
		return err
	}
	return s.receive(event["direction"].(string), decoded)
}
func decodeSpecHex(value string) ([]byte, error) { return hex.DecodeString(value) }
func specError(err error) any {
	if err == nil {
		return nil
	}
	var code Code
	if errors.As(err, &code) {
		return string(code)
	}
	return err.Error()
}
func canonicalSpec(value any) string { raw, _ := json.Marshal(value); return string(raw) }
func TestPublishedSessionAssumptions(t *testing.T) {
	var corpus struct {
		Environment struct{ Binding, Artifact map[string]any }
		Fixtures    map[string][]struct {
			Input json.RawMessage
			Error any
			After any
		}
		Cases []struct {
			Name  string
			Setup *string
			Steps []struct {
				Input json.RawMessage
				Error any
				After any
			}
		}
	}
	fixtureJSON(t, "testdata/session-vectors.json", &corpus)
	if len(corpus.Cases) != 68 {
		t.Fatal("changed session inventory")
	}
	for _, testCase := range corpus.Cases {
		t.Run(testCase.Name, func(t *testing.T) {
			s := newSpecModel(corpus.Environment.Binding, corpus.Environment.Artifact)
			steps := testCase.Steps
			if testCase.Setup != nil {
				steps = append(append(steps[:0:0], corpus.Fixtures[*testCase.Setup]...), steps...)
			}
			for index, step := range steps {
				err := applySpecStep(s, step.Input)
				if specError(err) != step.Error {
					t.Fatalf("step %d rejection: got %v want %v", index, specError(err), step.Error)
				}
				if canonicalSpec(s.snapshot()) != canonicalSpec(step.After) {
					t.Fatalf("step %d specification state differs: got %s want %s", index, canonicalSpec(s.snapshot()), canonicalSpec(step.After))
				}
			}
		})
	}
}
