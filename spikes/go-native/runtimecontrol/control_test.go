package runtimecontrol

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"testing"
)

type vector struct {
	Name     string  `json:"name"`
	JSON     *string `json:"json"`
	Hex      string  `json:"hex"`
	Expected string  `json:"expected"`
}
type shortReader struct{ *bytes.Reader }

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
func TestPublishedVectors(t *testing.T) {
	raw, e := os.ReadFile("testdata/control-vectors.json")
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct {
		Wire   []vector `json:"wire"`
		Binary []vector `json:"binary"`
		Max    int      `json:"max_frame_bytes"`
		Depth  int      `json:"max_depth"`
	}
	if e = json.Unmarshal(raw, &corpus); e != nil {
		t.Fatal(e)
	}
	if corpus.Max != MaxFrameBytes || corpus.Depth != MaxDepth || len(corpus.Wire) != 99 || len(corpus.Binary) != 7 {
		t.Fatal("reference corpus changed; reconcile specification")
	}
	for _, group := range []struct {
		name    string
		entries []vector
	}{{"wire", corpus.Wire}, {"binary", corpus.Binary}} {
		for _, v := range group.entries {
			t.Run(group.name+"/"+v.Name, func(t *testing.T) {
				raw, e := hex.DecodeString(v.Hex)
				if e != nil {
					t.Fatal(e)
				}
				if v.JSON != nil {
					raw = []byte(*v.JSON)
				}
				var f Frame
				if group.name == "wire" {
					f, e = Decode(raw)
				} else {
					f, e = Read(shortReader{bytes.NewReader(raw)})
				}
				if v.Expected == "ok" {
					if e != nil || f == nil {
						t.Fatalf("decode: %v", e)
					}
					var w shortWriter
					if e = Write(&w, f); e != nil {
						t.Fatal(e)
					}
					round, e := Read(shortReader{bytes.NewReader(w.Bytes())})
					if e != nil {
						t.Fatal(e)
					}
					a, _ := json.Marshal(f)
					b, _ := json.Marshal(round)
					if !bytes.Equal(a, b) {
						t.Fatal("roundtrip mismatch")
					}
				} else if v.Expected == "eof" {
					if e != nil || f != nil {
						t.Fatalf("EOF: %v", e)
					}
				} else if !errors.Is(e, Code(v.Expected)) {
					t.Fatalf("got %v, want %s", e, v.Expected)
				}
			})
		}
	}
}
