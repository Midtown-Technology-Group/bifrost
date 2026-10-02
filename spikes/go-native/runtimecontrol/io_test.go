package runtimecontrol

import (
	"bytes"
	"errors"
	"syscall"
	"testing"
)

type interruptedReader struct {
	*bytes.Reader
	interrupted bool
	partial     bool
}

func (r *interruptedReader) Read(p []byte) (int, error) {
	if !r.interrupted {
		r.interrupted = true
		if r.partial {
			n, _ := r.Reader.Read(p[:1])
			return n, syscall.EINTR
		}
		return 0, syscall.EINTR
	}
	return r.Reader.Read(p)
}

type interruptedWriter struct {
	bytes.Buffer
	interrupted bool
	partial     bool
}

func (w *interruptedWriter) Write(p []byte) (int, error) {
	if !w.interrupted {
		w.interrupted = true
		if w.partial {
			n, _ := w.Buffer.Write(p[:1])
			return n, syscall.EINTR
		}
		return 0, syscall.EINTR
	}
	return w.Buffer.Write(p)
}

type stoppedWriter struct{}

func (stoppedWriter) Write([]byte) (int, error) { return 0, nil }

func TestInterruptedPipeIO(t *testing.T) {
	frame, err := Decode([]byte(`{"protocol":"bifrost.runtime/v1","type":"Cancel","session_id":"00000000-0000-0000-0000-000000000001","message_id":"00000000-0000-0000-0000-000000000002","sequence":1,"correlation_id":null,"body":{"cancel_id":"00000000-0000-0000-0000-000000000003","reason":"requested","grace_ms":100}}`))
	if err != nil {
		t.Fatal(err)
	}
	for _, partial := range []bool{false, true} {
		w := &interruptedWriter{partial: partial}
		if err = Write(w, frame); err != nil {
			t.Fatal(err)
		}
		observed, err := Read(&interruptedReader{Reader: bytes.NewReader(w.Bytes()), partial: partial})
		if err != nil || observed["message_id"] != frame["message_id"] {
			t.Fatalf("interrupted pipe lost bytes: %v", err)
		}
	}
	if err = Write(stoppedWriter{}, frame); !errors.Is(err, IO) {
		t.Fatalf("non-progress writer: %v", err)
	}
}
