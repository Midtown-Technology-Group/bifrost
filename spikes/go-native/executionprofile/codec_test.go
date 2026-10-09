package executionprofile

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"syscall"
	"testing"
)

type specimen struct {
	Name   string `json:"name"`
	Hex    string `json:"hex"`
	Error  *Code  `json:"error"`
	Recipe *struct {
		Prefix string `json:"prefix_hex"`
		Byte   string `json:"repeat_byte_hex"`
		Count  int    `json:"count"`
		Suffix string `json:"suffix_hex"`
	} `json:"recipe"`
}

func corpus(t *testing.T) []specimen {
	t.Helper()
	raw, err := os.ReadFile("testdata/wire-vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var vectors []specimen
	if err = json.Unmarshal(raw, &vectors); err != nil {
		t.Fatal(err)
	}
	if len(vectors) != 94 {
		t.Fatal("proposal corpus changed; reconcile source and schema first")
	}
	return vectors
}

func specimenBytes(t *testing.T, vector specimen) []byte {
	t.Helper()
	decode := func(s string) []byte {
		raw, err := hex.DecodeString(s)
		if err != nil {
			t.Fatal(err)
		}
		return raw
	}
	if vector.Recipe != nil {
		r := vector.Recipe
		return append(append(decode(r.Prefix), bytes.Repeat(decode(r.Byte), r.Count)...), decode(r.Suffix)...)
	}
	return decode(vector.Hex)
}

func TestProposedWireCorpus(t *testing.T) {
	for _, vector := range corpus(t) {
		t.Run(vector.Name, func(t *testing.T) {
			raw := specimenBytes(t, vector)
			reader := bytes.NewReader(raw)
			_, err := Read(reader)
			if len(raw) == 0 && vector.Error == nil {
				if !errors.Is(err, io.EOF) {
					t.Fatal("clean EOF must remain transport loss, not a frame")
				}
				return
			}
			if err == nil && reader.Len() != 0 {
				err = InvalidFrame // specimen is exactly one frame; streams may concatenate
			}
			if vector.Error == nil && err != nil {
				t.Fatal(err)
			}
			if vector.Error != nil && !errors.Is(err, *vector.Error) {
				t.Fatalf("expected %s, got %v", *vector.Error, err)
			}
		})
	}
}

func validSpecimen(t *testing.T) []byte {
	t.Helper()
	for _, vector := range corpus(t) {
		if vector.Error == nil && vector.Recipe == nil && vector.Hex != "" {
			return specimenBytes(t, vector)
		}
	}
	t.Fatal("no valid proposed frame")
	return nil
}

type shortReader struct{ io.Reader }

func (r shortReader) Read(p []byte) (int, error) {
	if len(p) > 1 {
		p = p[:1]
	}
	return r.Reader.Read(p)
}

type shortWriter struct{ bytes.Buffer }

func (w *shortWriter) Write(p []byte) (int, error) {
	if len(p) > 1 {
		p = p[:1]
	}
	return w.Buffer.Write(p)
}

func TestPartialIOAndConcatenatedStream(t *testing.T) {
	raw := validSpecimen(t)
	reader := shortReader{bytes.NewReader(append(bytes.Clone(raw), raw...))}
	for range 2 {
		frame, err := Read(reader)
		if err != nil {
			t.Fatal(err)
		}
		writer := &shortWriter{}
		if err = Write(writer, frame.Frame); err != nil {
			t.Fatal(err)
		}
		if _, err = Read(bytes.NewReader(writer.Bytes())); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := Read(reader); !errors.Is(err, io.EOF) {
		t.Fatal("stream EOF lost", err)
	}
	for _, length := range []int{1, 3, 4, len(raw) - 1} {
		if _, err := Read(shortReader{bytes.NewReader(raw[:length])}); !errors.Is(err, TruncatedFrame) {
			t.Fatalf("prefix/body truncation at %d: %v", length, err)
		}
	}
}

type interruptedReader struct {
	io.Reader
	first bool
}

func (r *interruptedReader) Read(p []byte) (int, error) {
	if !r.first {
		r.first = true
		return 0, syscall.EINTR
	}
	n, err := r.Reader.Read(p)
	if n > 0 {
		return n, syscall.EINTR
	}
	return n, err
}

type interruptedWriter struct{ bytes.Buffer }

func (w *interruptedWriter) Write(p []byte) (int, error) {
	if len(p) > 1 {
		p = p[:1]
	}
	n, _ := w.Buffer.Write(p)
	return n, syscall.EINTR
}

func TestInterruptedIOPreservesBytes(t *testing.T) {
	frame, err := Read(&interruptedReader{Reader: bytes.NewReader(validSpecimen(t))})
	if err != nil {
		t.Fatal(err)
	}
	writer := &interruptedWriter{}
	if err = Write(writer, frame.Frame); err != nil {
		t.Fatal(err)
	}
	if _, err = Read(bytes.NewReader(writer.Bytes())); err != nil {
		t.Fatal(err)
	}
}

func TestRawPayloadDigestAndIsolation(t *testing.T) {
	raw := validSpecimen(t)[4:]
	frame, err := Decode(raw)
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(raw)
	if frame.PayloadSHA256() != hex.EncodeToString(digest[:]) {
		t.Fatal("digest does not bind exact payload")
	}
	spaced, err := Decode(append([]byte(" \n"), raw...))
	if err != nil || spaced.PayloadSHA256() == frame.PayloadSHA256() {
		t.Fatal("whitespace was normalized out of payload digest", err)
	}
	before := frame.PayloadSHA256()
	raw[0] = '!'
	frame.Frame["type"] = "mutated decoded view"
	if frame.PayloadSHA256() != before {
		t.Fatal("input or decoded view mutated retained payload")
	}
}

func TestPinnedSchemaAndCorpusBytes(t *testing.T) {
	raw, err := os.ReadFile("testdata/provenance.json")
	if err != nil {
		t.Fatal(err)
	}
	var provenance struct {
		Head  string `json:"proposal_head"`
		Files map[string]struct {
			SHA256 string `json:"sha256"`
		} `json:"files"`
	}
	if json.Unmarshal(raw, &provenance) != nil || provenance.Head != "35daf020d5e1286f82bdfed6bb3fc537bf3d78f6" || len(provenance.Files) != 4 {
		t.Fatal("unreviewed proposal provenance")
	}
	for path, expected := range provenance.Files {
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		digest := sha256.Sum256(data)
		if hex.EncodeToString(digest[:]) != expected.SHA256 {
			t.Fatal("changed shared document", path)
		}
	}
}
