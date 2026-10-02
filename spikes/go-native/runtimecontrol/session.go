package runtimecontrol

import "encoding/json"

const InvalidBinding Code = "InvalidBinding"
const InvalidTransition Code = "InvalidTransition"

// Binding is a parent input obtained from accepted preparation and an actual
// supervised handle. Constructing it neither prepares nor admits an execution.
type Binding struct {
	Supervisor    string      `json:"supervisor_incarnation_id"`
	Session       string      `json:"session_id"`
	Process       string      `json:"process_identity"`
	LogicalKind   string      `json:"logical_kind"`
	LogicalID     string      `json:"logical_id"`
	AttemptKind   string      `json:"attempt_kind"`
	AttemptID     string      `json:"attempt_id"`
	AttemptNumber json.Number `json:"attempt_number"`
	Prepare       string      `json:"prepare_message_id"`
	Artifact      string      `json:"expected_artifact_id"`
	Image         *string     `json:"expected_image_digest"`
}

// Authorization asserts a parent-observed durable commit. It does not perform
// SQL, inspect a lease or substitute for committed coordinator authority.
type Authorization struct {
	Commit   string      `json:"committed_start_id"`
	Duration json.Number `json:"parent_duration_seconds"`
}
type Direction string

const ParentToRuntime Direction = "ParentToRuntime"
const RuntimeToParent Direction = "RuntimeToParent"

type State string

const AwaitHello State = "AwaitHello"
const Prepared State = "Prepared"
const Executing State = "Executing"
const Cancelling State = "Cancelling"
const StoppedObserved State = "StoppedObserved"

type Session struct {
	binding                         Binding
	state                           State
	parentSequence, runtimeSequence uint64
	authorization                   *Authorization
	start, cancel                   any
	childStart, childCancel         bool
	elapsed                         uint64
}

func NewSession(b Binding) (*Session, error) {
	pair := b.LogicalKind == "workflow" && b.AttemptKind == "workflow_execution_attempt" || b.LogicalKind == "agent_run" && b.AttemptKind == "execution_attempt"
	if !pair || b.Process == "" || b.Artifact == "" || (b.Image != nil && *b.Image == "") || !id(b.Supervisor) || !id(b.Session) || !id(b.LogicalID) || !id(b.AttemptID) || !id(b.Prepare) || !uintValue(b.AttemptNumber, true) {
		return nil, InvalidBinding
	}
	if b.Image != nil {
		image := *b.Image
		b.Image = &image
	}
	return &Session{binding: b, state: AwaitHello}, nil
}
func (s *Session) State() State { return s.state }
func (s *Session) Authorize(a Authorization) error {
	if s.state != Prepared || s.authorization != nil {
		return InvalidTransition
	}
	if !id(a.Commit) || !uintValue(a.Duration, true) {
		return InvalidFrame
	}
	s.authorization = &a
	return nil
}
func numeric(v any) uint64 { n, _ := v.(json.Number).Int64(); return uint64(n) }
func (s *Session) Accept(direction Direction, process string, supplied Frame) error {
	raw, e := json.Marshal(supplied)
	if e != nil {
		return InvalidFrame
	}
	f, e := Decode(raw)
	if e != nil {
		return e
	}
	if process != s.binding.Process || f["session_id"] != s.binding.Session {
		return InvalidBinding
	}
	current := s.runtimeSequence
	if direction == ParentToRuntime {
		current = s.parentSequence
	}
	if (direction != ParentToRuntime && direction != RuntimeToParent) || numeric(f["sequence"]) != current+1 || s.state == StoppedObserved {
		return InvalidTransition
	}
	body := f["body"].(map[string]any)
	kind := f["type"].(string)
	next := s.state
	switch {
	case kind == "Hello" && direction == RuntimeToParent && s.state == AwaitHello:
		hasProtocol := false
		for _, p := range body["supported_protocols"].([]any) {
			hasProtocol = hasProtocol || p == Protocol
		}
		if !hasProtocol {
			return UnsupportedProtocol
		}
		caps := body["capabilities"].([]any)
		a := body["artifact"].(map[string]any)
		if len(caps) != 1 || caps[0] != "control_profile/v1" || a["artifact_id"] != s.binding.Artifact || (s.binding.Image != nil && a["image_digest"] != *s.binding.Image) {
			return InvalidBinding
		}
		next = Prepared
	case kind == "Start" && direction == ParentToRuntime && s.state == Prepared:
		if s.authorization == nil {
			return InvalidTransition
		}
		if body["prepare_message_id"] != s.binding.Prepare || body["committed_start_id"] != s.authorization.Commit || body["parent_duration_seconds"] != s.authorization.Duration {
			return InvalidBinding
		}
		next = Executing
	case kind == "Heartbeat" && direction == RuntimeToParent:
		state := body["state"]
		start := body["start_message_id"]
		matches := state == "prepared" && !s.childStart && !s.childCancel && start == nil || state == "executing" && s.start != nil && !s.childCancel && start == s.start || state == "cancelling" && s.cancel != nil && start == s.start
		if !matches || s.state == AwaitHello || numeric(body["monotonic_elapsed_ms"]) < s.elapsed {
			return InvalidTransition
		}
	case kind == "Cancel" && direction == ParentToRuntime && (s.state == Prepared || s.state == Executing):
		next = Cancelling
	case kind == "Stopped" && direction == RuntimeToParent && s.state != AwaitHello:
		start, cancel, reason, result := body["start_message_id"], body["cancel_id"], body["reason"], body["result_message_id"]
		startMatches := start == s.start || !s.childStart && start == nil && (reason == "prepare_rejected" || reason == "protocol_error")
		cancelMatches := cancel == s.cancel || !s.childCancel && cancel == nil
		if !startMatches || !cancelMatches {
			return InvalidBinding
		}
		if result != nil && start == nil || reason == "completed" && (start == nil || s.childCancel || result == nil) || reason == "cancelled" && (s.state != Cancelling || cancel == nil) || reason == "prepare_rejected" && start != nil {
			return InvalidTransition
		}
		next = StoppedObserved
	default:
		return InvalidTransition
	}
	// Only accepted input advances any frontier. Rejection consumes no sequence.
	switch kind {
	case "Start":
		s.start = f["message_id"]
	case "Cancel":
		s.cancel = body["cancel_id"]
	case "Heartbeat":
		s.elapsed = numeric(body["monotonic_elapsed_ms"])
		s.childStart = s.childStart || body["start_message_id"] != nil
		s.childCancel = s.childCancel || body["state"] == "cancelling"
	}
	if direction == ParentToRuntime {
		s.parentSequence = numeric(f["sequence"])
	} else {
		s.runtimeSequence = numeric(f["sequence"])
	}
	s.state = next
	return nil
}
