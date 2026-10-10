//go:build linux && amd64

package nativeadapter

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"os"
	"strings"
	"testing"
)

func testDigest(raw []byte) string { sum := sha256.Sum256(raw); return hex.EncodeToString(sum[:]) }

func TestSealedBytesRemainImmutable(t *testing.T) {
	raw := []byte("not executed: representative accepted bytes")
	executable, err := SealExecutable(bytes.NewReader(raw), testDigest(raw))
	if err != nil {
		t.Fatal(err)
	}
	defer executable.Close()
	if _, err := executable.file.WriteAt([]byte("changed"), 0); err == nil {
		t.Fatal("sealed bytes writable")
	}
	if err := executable.file.Truncate(1); err == nil {
		t.Fatal("sealed bytes shrinkable")
	}
	if err := executable.file.Truncate(int64(len(raw) + 1)); err == nil {
		t.Fatal("sealed bytes growable")
	}
	actual, err := os.ReadFile(executable.Path())
	if err != nil || !bytes.Equal(actual, raw) {
		t.Fatal("sealed bytes changed")
	}
	if err := executable.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.ReadFile(executable.Path()); err == nil {
		t.Fatal("closed artifact accessible")
	}
}

func TestExecutableDigestAndBounds(t *testing.T) {
	raw := []byte("accepted bytes")
	for name, test := range map[string]struct {
		source io.Reader
		digest string
	}{
		"different-bytes":  {bytes.NewReader([]byte("tampered")), testDigest(raw)},
		"empty":            {bytes.NewReader(nil), testDigest(nil)},
		"malformed-digest": {bytes.NewReader(raw), "source-commit"},
		"uppercase-digest": {bytes.NewReader(raw), strings.ToUpper(testDigest(raw))},
		"oversized":        {io.LimitReader(zeroReader{}, MaxExecutableBytes+1), testDigest(raw)},
	} {
		t.Run(name, func(t *testing.T) {
			if executable, err := SealExecutable(test.source, test.digest); err != ArtifactRejected || executable != nil {
				if executable != nil {
					_ = executable.Close()
				}
				t.Fatal("unaccepted bytes materialized")
			}
		})
	}
}

type zeroReader struct{}

func (zeroReader) Read(p []byte) (int, error) { clear(p); return len(p), nil }
