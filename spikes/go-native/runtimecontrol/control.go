// Package runtimecontrol implements only the published BiFrost C1-P0 wire
// profile. It has no execution, credential, lifecycle or database authority.
package runtimecontrol

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"math"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

const Protocol = "bifrost.runtime/v1"
const MaxFrameBytes = 16 * 1024 * 1024
const MaxDepth = 64

type Code string

func (c Code) Error() string { return "runtime control protocol: " + string(c) }

const (
	InvalidJSON         Code = "InvalidJson"
	InvalidFrame        Code = "InvalidFrame"
	UnsupportedProtocol Code = "UnsupportedProtocol"
	UnsupportedFrame    Code = "UnsupportedFrame"
	FrameTooLarge       Code = "FrameTooLarge"
	TruncatedFrame      Code = "TruncatedFrame"
	IO                  Code = "Io"
)

type Frame map[string]any

var uuid = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
var hash = regexp.MustCompile(`^[0-9a-f]{64}$`)

// Go's standard JSON parser replaces lone UTF-16 surrogates. The contract
// rejects them instead; inspect escape spelling before that information is lost.
func validEscapes(raw []byte) bool {
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
		n, e := strconv.ParseUint(string(raw[i+1:i+5]), 16, 16)
		if e != nil {
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
			low, e := strconv.ParseUint(string(raw[i+3:i+7]), 16, 16)
			if e != nil || low < 0xdc00 || low > 0xdfff {
				return false
			}
			i += 6
		}
	}
	return true
}
func tree(d *json.Decoder, depth int) (any, error) {
	token, e := d.Token()
	if e != nil {
		return nil, InvalidJSON
	}
	if delimiter, ok := token.(json.Delim); ok {
		if depth >= MaxDepth {
			return nil, InvalidJSON
		}
		switch delimiter {
		case '{':
			object := map[string]any{}
			for d.More() {
				key, e := d.Token()
				if e != nil {
					return nil, InvalidJSON
				}
				name, ok := key.(string)
				if !ok {
					return nil, InvalidJSON
				}
				if _, present := object[name]; present {
					return nil, InvalidJSON
				}
				value, e := tree(d, depth+1)
				if e != nil {
					return nil, e
				}
				object[name] = value
			}
			end, e := d.Token()
			if e != nil || end != json.Delim('}') {
				return nil, InvalidJSON
			}
			return object, nil
		case '[':
			array := []any{}
			for d.More() {
				value, e := tree(d, depth+1)
				if e != nil {
					return nil, e
				}
				array = append(array, value)
			}
			end, e := d.Token()
			if e != nil || end != json.Delim(']') {
				return nil, InvalidJSON
			}
			return array, nil
		default:
			return nil, InvalidJSON
		}
	}
	if n, ok := token.(json.Number); ok {
		f, e := strconv.ParseFloat(string(n), 64)
		if e != nil || math.IsInf(f, 0) || math.IsNaN(f) {
			return nil, InvalidJSON
		}
	}
	return token, nil
}
func fields(value any, names ...string) (map[string]any, bool) {
	object, ok := value.(map[string]any)
	if !ok || len(object) != len(names) {
		return nil, false
	}
	for _, name := range names {
		if _, ok := object[name]; !ok {
			return nil, false
		}
	}
	return object, true
}
func text(v any, nonempty bool) bool            { s, ok := v.(string); return ok && (!nonempty || s != "") }
func id(v any) bool                             { s, ok := v.(string); return ok && uuid.MatchString(s) }
func optional(v any, check func(any) bool) bool { return v == nil || check(v) }
func name(v any) bool                           { return text(v, true) }
func uintValue(v any, positive bool) bool {
	n, ok := v.(json.Number)
	if !ok || strings.HasPrefix(string(n), "-") {
		return false
	}
	u, e := strconv.ParseUint(string(n), 10, 64)
	return e == nil && u <= 9007199254740991 && (!positive || u > 0)
}
func choice(v any, options ...string) bool {
	s, ok := v.(string)
	if !ok {
		return false
	}
	for _, o := range options {
		if s == o {
			return true
		}
	}
	return false
}
func names(v any) bool {
	items, ok := v.([]any)
	if !ok {
		return false
	}
	seen := map[string]bool{}
	for _, item := range items {
		if !name(item) {
			return false
		}
		s := item.(string)
		if seen[s] {
			return false
		}
		seen[s] = true
	}
	return true
}
func artifact(v any) bool {
	a, ok := fields(v, "artifact_id", "image_digest", "interpreter", "sdk", "requirements_lock_sha256", "runtime_protocol")
	if !ok {
		return false
	}
	i, ok := fields(a["interpreter"], "implementation", "version")
	if !ok || !name(i["implementation"]) || !name(i["version"]) {
		return false
	}
	s, ok := fields(a["sdk"], "distribution", "version")
	if !ok || !optional(s["distribution"], name) || !optional(s["version"], name) {
		return false
	}
	return name(a["artifact_id"]) && optional(a["image_digest"], name) && a["runtime_protocol"] == Protocol && optional(a["requirements_lock_sha256"], func(v any) bool { s, ok := v.(string); return ok && hash.MatchString(s) })
}
func runtimeError(v any) bool {
	e, ok := fields(v, "type", "message", "traceback")
	return ok && name(e["type"]) && text(e["message"], false) && optional(e["traceback"], func(v any) bool { return text(v, false) })
}
func validateBody(kind string, v any) bool {
	switch kind {
	case "Hello":
		b, ok := fields(v, "runtime_incarnation_id", "supported_protocols", "capabilities", "artifact")
		return ok && id(b["runtime_incarnation_id"]) && names(b["supported_protocols"]) && names(b["capabilities"]) && artifact(b["artifact"])
	case "Start":
		b, ok := fields(v, "prepare_message_id", "committed_start_id", "parent_duration_seconds")
		return ok && id(b["prepare_message_id"]) && id(b["committed_start_id"]) && uintValue(b["parent_duration_seconds"], true)
	case "Heartbeat":
		b, ok := fields(v, "start_message_id", "state", "monotonic_elapsed_ms")
		return ok && optional(b["start_message_id"], id) && choice(b["state"], "prepared", "executing", "cancelling") && uintValue(b["monotonic_elapsed_ms"], false)
	case "Cancel":
		b, ok := fields(v, "cancel_id", "reason", "grace_ms")
		return ok && id(b["cancel_id"]) && choice(b["reason"], "requested", "deadline", "authority_revoked", "supervisor_shutdown") && uintValue(b["grace_ms"], false)
	case "Stopped":
		b, ok := fields(v, "start_message_id", "cancel_id", "reason", "result_message_id", "error")
		return ok && optional(b["start_message_id"], id) && optional(b["cancel_id"], id) && choice(b["reason"], "completed", "cancelled", "prepare_rejected", "protocol_error") && optional(b["result_message_id"], id) && optional(b["error"], runtimeError)
	}
	return false
}
func Decode(raw []byte) (Frame, error) {
	if len(raw) > MaxFrameBytes {
		return nil, FrameTooLarge
	}
	if !utf8.Valid(raw) || !validEscapes(raw) {
		return nil, InvalidJSON
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	value, e := tree(d, 0)
	if e != nil {
		return nil, e
	}
	if _, e = d.Token(); !errors.Is(e, io.EOF) {
		return nil, InvalidJSON
	}
	f, ok := fields(value, "protocol", "type", "session_id", "message_id", "sequence", "correlation_id", "body")
	if !ok {
		return nil, InvalidFrame
	}
	if !name(f["protocol"]) {
		return nil, InvalidFrame
	}
	if f["protocol"] != Protocol {
		return nil, UnsupportedProtocol
	}
	if !name(f["type"]) {
		return nil, InvalidFrame
	}
	kind := f["type"].(string)
	if !choice(kind, "Hello", "Start", "Heartbeat", "Cancel", "Stopped") {
		return nil, UnsupportedFrame
	}
	if !id(f["session_id"]) || !id(f["message_id"]) || !uintValue(f["sequence"], true) || !validateBody(kind, f["body"]) {
		return nil, InvalidFrame
	}
	if kind == "Start" {
		if !id(f["correlation_id"]) || f["correlation_id"] != f["body"].(map[string]any)["prepare_message_id"] {
			return nil, InvalidFrame
		}
	} else if f["correlation_id"] != nil {
		return nil, InvalidFrame
	}
	return Frame(f), nil
}
func Read(r io.Reader) (Frame, error) {
	var prefix [4]byte
	n, e := io.ReadFull(r, prefix[:])
	if errors.Is(e, io.EOF) && n == 0 {
		return nil, nil
	}
	if e != nil {
		if errors.Is(e, io.EOF) || errors.Is(e, io.ErrUnexpectedEOF) {
			return nil, TruncatedFrame
		}
		return nil, IO
	}
	size := binary.BigEndian.Uint32(prefix[:])
	if size > MaxFrameBytes {
		return nil, FrameTooLarge
	}
	raw := make([]byte, size)
	if _, e = io.ReadFull(r, raw); e != nil {
		if errors.Is(e, io.EOF) || errors.Is(e, io.ErrUnexpectedEOF) {
			return nil, TruncatedFrame
		}
		return nil, IO
	}
	return Decode(raw)
}
func Write(w io.Writer, f Frame) error {
	raw, e := json.Marshal(f)
	if e != nil {
		return InvalidFrame
	}
	if _, e = Decode(raw); e != nil {
		return e
	}
	prefix := make([]byte, 4)
	binary.BigEndian.PutUint32(prefix, uint32(len(raw)))
	for _, data := range [][]byte{prefix, raw} {
		for len(data) > 0 {
			n, e := w.Write(data)
			if e != nil {
				return IO
			}
			if n <= 0 || n > len(data) {
				return IO
			}
			data = data[n:]
		}
	}
	return nil
}
