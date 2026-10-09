// Package executionprofile prototypes the proposed common execution profile.
// It validates wire data only. It cannot admit, provision, launch or finalize.
package executionprofile

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"regexp"
	"syscall"
	"unicode/utf8"
)

const Protocol = "bifrost.runtime/v1"
const Profile = "execution_profile/v1"
const MaxFrameBytes = 16 * 1024 * 1024
const MaxDepth = 64

type Code string

func (c Code) Error() string { return "proposed runtime profile: " + string(c) }

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

// Decoded retains the original payload independently of the mutable decoded
// view. Receipt hashing must never reserialize the view or business value.
type Decoded struct {
	Frame   Frame
	payload []byte
}

func (d Decoded) PayloadSHA256() string {
	digest := sha256.Sum256(d.payload)
	return hex.EncodeToString(digest[:])
}

var uuid = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

func Decode(raw []byte) (Decoded, error) {
	if len(raw) > MaxFrameBytes {
		return Decoded{}, FrameTooLarge
	}
	if !utf8.Valid(raw) || !validEscapes(raw) {
		return Decoded{}, InvalidJSON
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	value, err := tree(decoder, 0)
	if err != nil {
		return Decoded{}, err
	}
	if _, err = decoder.Token(); !errors.Is(err, io.EOF) {
		return Decoded{}, InvalidJSON
	}
	frame, ok := value.(map[string]any)
	if !ok || len(frame) != 7 {
		return Decoded{}, InvalidFrame
	}
	for _, key := range []string{"protocol", "type", "session_id", "message_id", "sequence", "correlation_id", "body"} {
		if _, present := frame[key]; !present {
			return Decoded{}, InvalidFrame
		}
	}
	protocol, ok := frame["protocol"].(string)
	if !ok {
		return Decoded{}, InvalidFrame
	}
	if protocol != Protocol {
		return Decoded{}, UnsupportedProtocol
	}
	kind, ok := frame["type"].(string)
	if !ok || kind == "" {
		return Decoded{}, InvalidFrame
	}
	for _, key := range []string{"session_id", "message_id", "correlation_id"} {
		if key == "correlation_id" && frame[key] == nil {
			continue
		}
		id, ok := frame[key].(string)
		if !ok || !uuid.MatchString(id) {
			return Decoded{}, InvalidFrame
		}
	}
	n, ok := integer(frame["sequence"])
	if !ok || n < 1 || n > 9007199254740991 {
		return Decoded{}, InvalidFrame
	}
	root := registry[profileID]
	if root == nil {
		return Decoded{}, InvalidFrame
	}
	if _, present := root["$defs"].(map[string]any)[kind]; !present {
		return Decoded{}, UnsupportedFrame
	}
	if !valid(root, frame, root) {
		return Decoded{}, InvalidFrame
	}
	body := frame["body"].(map[string]any)
	correlation := frame["correlation_id"]
	field := map[string]string{
		"Prepared": "prepare_message_id", "Start": "prepare_message_id",
		"Provision": "prepare_message_id", "LogBatch": "start_message_id",
		"Usage": "start_message_id", "Result": "start_message_id",
		"ResultReceipt": "result_message_id",
	}[kind]
	if field != "" && correlation != body[field] {
		return Decoded{}, InvalidFrame
	}
	switch kind {
	case "Offer", "Heartbeat", "Cancel", "Stopped":
		if correlation != nil {
			return Decoded{}, InvalidFrame
		}
	case "Select", "Prepare":
		if correlation == nil {
			return Decoded{}, InvalidFrame
		}
	}
	return Decoded{Frame: frame, payload: bytes.Clone(raw)}, nil
}

// Read is a stream operation. A clean EOF is distinct from truncated framing.
func Read(reader io.Reader) (Decoded, error) {
	reader = interrupted{reader}
	var prefix [4]byte
	n, err := io.ReadFull(reader, prefix[:])
	if n == 0 && errors.Is(err, io.EOF) {
		return Decoded{}, io.EOF
	}
	if err != nil {
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			return Decoded{}, TruncatedFrame
		}
		return Decoded{}, IO
	}
	size := binary.BigEndian.Uint32(prefix[:])
	if size == 0 {
		return Decoded{}, InvalidFrame
	}
	if size > MaxFrameBytes {
		return Decoded{}, FrameTooLarge
	}
	raw := make([]byte, size)
	if _, err = io.ReadFull(reader, raw); err != nil {
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			return Decoded{}, TruncatedFrame
		}
		return Decoded{}, IO
	}
	return Decode(raw)
}

type interrupted struct{ io.Reader }

func (r interrupted) Read(p []byte) (int, error) {
	for {
		n, err := r.Reader.Read(p)
		if errors.Is(err, syscall.EINTR) {
			if n != 0 {
				return n, nil
			}
			continue
		}
		return n, err
	}
}

func Write(writer io.Writer, frame Frame) error {
	raw, err := json.Marshal(frame)
	if err != nil {
		return InvalidFrame
	}
	if _, err = Decode(raw); err != nil {
		return err
	}
	framed := make([]byte, 4+len(raw))
	binary.BigEndian.PutUint32(framed[:4], uint32(len(raw)))
	copy(framed[4:], raw)
	for len(framed) != 0 {
		n, err := writer.Write(framed)
		if n < 0 || n > len(framed) {
			return IO
		}
		framed = framed[n:]
		if err != nil && !errors.Is(err, syscall.EINTR) {
			return IO
		}
		if n == 0 && err == nil {
			return IO
		}
	}
	return nil
}
