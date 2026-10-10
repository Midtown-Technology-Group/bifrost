//go:build linux && amd64

package main

import (
	"bytes"
	"encoding/json"
	"os"
	"testing"
	"time"

	"github.com/midtown-technology-group/bifrost-go/executionprofile"
)

func TestFixtureUTCPrecisionPreservesCommonContract(t *testing.T) {
	raw, err := os.ReadFile("../../executionprofile/testdata/structural-vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var vectors []struct {
		Name  string          `json:"name"`
		Frame json.RawMessage `json:"frame"`
	}
	if json.Unmarshal(raw, &vectors) != nil {
		t.Fatal("invalid published vectors")
	}
	value := time.Date(2026, 10, 10, 21, 53, 55, 123456789, time.UTC)
	for _, vector := range vectors {
		if vector.Name != "valid-Prepare" && vector.Name != "valid-Provision" {
			continue
		}
		decoded, err := executionprofile.Decode(vector.Frame)
		if err != nil {
			t.Fatal(err)
		}
		body := decoded.Frame["body"].(map[string]any)
		field := "expires_at"
		if vector.Name == "valid-Prepare" {
			body = body["workload"].(map[string]any)
			field = "deadline_utc"
		}
		body[field] = value.Format(time.RFC3339Nano)
		var out bytes.Buffer
		if executionprofile.Write(&out, decoded.Frame) != executionprofile.InvalidFrame {
			t.Fatal("nanosecond fixture unexpectedly conforms")
		}
		body[field] = utcText(value)
		if executionprofile.Write(&out, decoded.Frame) != nil {
			t.Fatal("microsecond fixture rejected")
		}
		parsed, err := time.Parse(time.RFC3339Nano, body[field].(string))
		if err != nil || parsed.After(value) || parsed.Nanosecond() != 123456000 {
			t.Fatal("deadline widened or precision changed")
		}
	}
}
