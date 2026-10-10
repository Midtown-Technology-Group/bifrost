//go:build linux && amd64

// Protocolprobe is a non-Rust local supervisor using published common frames.
// Its Start and receipt decisions are synthetic; it owns no durable lifecycle.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sync/atomic"
	"time"

	"github.com/midtown-technology-group/bifrost-go/executionprofile"
)

var rejected = errors.New("common protocol fixture rejected")

func digest(raw []byte) string { sum := sha256.Sum256(raw); return hex.EncodeToString(sum[:]) }
func fileDigest(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", rejected
	}
	defer f.Close()
	h := sha256.New()
	if _, err = io.Copy(h, f); err != nil {
		return "", rejected
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
func encode(value any) ([]byte, error) { return json.Marshal(value) }
func fixtureID(n int) string           { return fmt.Sprintf("00000000-0000-0000-0000-%012x", n) }

type fixture struct{ adapter, workflow, evidence, graph, inputSchema, outputSchema, vectors string }
type observation struct {
	Case                string                   `json:"case"`
	Frames              []executionprofile.Frame `json:"frames"`
	SDKCalls            int64                    `json:"sdk_calls"`
	SDKCancelled        bool                     `json:"sdk_cancelled"`
	ElapsedMS           float64                  `json:"elapsed_ms"`
	ResultPayloadSHA256 string                   `json:"result_payload_sha256,omitempty"`
}

func runCase(f fixture, mode string) (observation, error) {
	result := observation{Case: mode}
	base := map[string]int{"success": 1000, "success-again": 2000, "missing": 3000, "cancel-before-material": 4000, "cancel-running": 5000}[mode]
	id := func(n int) string { return fixtureID(base + n) }
	started := time.Now()
	directory, err := os.MkdirTemp("", "bifrost-common-session-")
	if err != nil {
		return result, rejected
	}
	defer os.RemoveAll(directory)
	vectorBytes, err := os.ReadFile(f.vectors)
	if err != nil {
		return result, rejected
	}
	var vectors []struct {
		Name  string          `json:"name"`
		Frame json.RawMessage `json:"frame"`
	}
	if json.Unmarshal(vectorBytes, &vectors) != nil {
		return result, rejected
	}
	var prepare executionprofile.Frame
	for _, v := range vectors {
		if v.Name == "valid-Prepare" {
			decoded, err := executionprofile.Decode(v.Frame)
			if err != nil {
				return result, rejected
			}
			prepare = decoded.Frame
		}
	}
	if prepare == nil {
		return result, rejected
	}
	body := prepare["body"].(map[string]any)
	binding := body["binding"].(map[string]any)
	for _, name := range []string{"execution_id", "attempt_id", "session_id", "runtime_incarnation_id"} {
		n := map[string]int{"execution_id": 1, "attempt_id": 2, "session_id": 5, "runtime_incarnation_id": 7}[name]
		binding[name] = id(n)
		if name == "execution_id" || name == "attempt_id" {
			body["context"].(map[string]any)[name] = id(n)
		}
	}
	prepare["session_id"] = binding["session_id"]
	prepare["message_id"] = id(102)
	org := binding["original_caller"].(map[string]any)["organization_id"].(string)
	scope := map[string]any{"kind": "organization", "organization_id": org}
	binding["effective_scope"] = scope
	body["context"].(map[string]any)["effective_scope"] = scope
	artifact := body["artifact"].(map[string]any)
	childDigest, err := fileDigest(f.workflow)
	if err != nil || childDigest != "160917deeb94275f31ca9ddee2dacae00fb079fdf54cb60e206c8def261f9c34" {
		return result, rejected
	}
	adapterDigest, err := fileDigest(f.adapter)
	if err != nil {
		return result, rejected
	}
	artifact["executable_sha256"] = childDigest
	artifact["adapter_sha256"] = adapterDigest
	artifact["sdk"] = map[string]any{"distribution": "github.com/midtown-technology-group/bifrost-go", "version": "0.0.0-spike.2"}
	artifact["toolchain"] = map[string]any{"implementation": "go", "version": "go1.27.1"}
	contents := map[string][]byte{}
	for name, path := range map[string]string{"build-evidence.json": f.evidence, "module-graph.txt": f.graph, "input-schema.json": f.inputSchema, "output-schema.json": f.outputSchema} {
		raw, err := os.ReadFile(path)
		if err != nil {
			return result, rejected
		}
		contents[name] = raw
		if os.WriteFile(filepath.Join(directory, name), raw, 0600) != nil {
			return result, rejected
		}
	}
	if os.Symlink(f.workflow, filepath.Join(directory, "workflow")) != nil {
		return result, rejected
	}
	artifact["build_evidence_sha256"] = digest(contents["build-evidence.json"])
	artifact["dependencies"] = map[string]any{"kind": "go-module-graph/v1", "digest": "sha256:" + digest(contents["module-graph.txt"])}
	// Fixture-local identity only; not an accepted deployment identity recipe.
	artifactIdentity, _ := encode(map[string]any{"child": childDigest, "adapter": adapterDigest, "evidence": artifact["build_evidence_sha256"]})
	artifactID := "sha256:" + digest(artifactIdentity)
	artifact["artifact_id"] = artifactID
	binding["artifact_id"] = artifactID
	body["context"].(map[string]any)["artifact_id"] = artifactID
	workload := body["workload"].(map[string]any)
	workload["input_schema_digest"] = "sha256:" + digest(contents["input-schema.json"])
	workload["output_schema_digest"] = "sha256:" + digest(contents["output-schema.json"])
	workload["deadline_utc"] = time.Now().Add(10 * time.Second).UTC().Format(time.RFC3339Nano)
	keys := []string{"credential", "enabled"}
	if mode == "missing" {
		keys = []string{"zeta", "alpha", "credential"}
	}
	workload["input"] = map[string]any{"integration_name": "Fixture", "required_keys": keys}
	index, _ := encode(map[string]any{"version": "isolated-native-session-bundle/v1", "binding": binding, "artifact": artifact, "input_schema_sha256": digest(contents["input-schema.json"]), "output_schema_sha256": digest(contents["output-schema.json"])})
	if os.WriteFile(filepath.Join(directory, "session-bundle.json"), index, 0600) != nil {
		return result, rejected
	}
	var calls atomic.Int64
	entered := make(chan struct{}, 1)
	sdkCancelled := make(chan struct{}, 1)
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request struct {
			Name  string `json:"name"`
			Scope string `json:"scope"`
		}
		if r.Method != "POST" || r.URL.Path != "/api/sdk/integrations/get" || r.Header.Get("Authorization") != "Bearer synthetic-common-fixture" || json.NewDecoder(r.Body).Decode(&request) != nil || request.Name != "Fixture" || request.Scope != org {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		calls.Add(1)
		if mode == "cancel-running" {
			entered <- struct{}{}
			<-r.Context().Done()
			sdkCancelled <- struct{}{}
			return
		}
		_, _ = w.Write([]byte(`{"integration_id":"fixture","config":{"credential":"synthetic-secret","enabled":false}}`))
	}))
	defer func() { server.CloseClientConnections(); server.Close() }()
	ca := string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw}))
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, f.adapter, "--bundle", directory, "--index-sha256", digest(index))
	cmd.Env = []string{"PATH=/nonexistent"}
	cmd.Stderr = io.Discard
	cmd.WaitDelay = 100 * time.Millisecond
	parent, err := cmd.StdinPipe()
	if err != nil {
		return result, rejected
	}
	defer parent.Close()
	runtime, err := cmd.StdoutPipe()
	if err != nil {
		return result, rejected
	}
	defer runtime.Close()
	material, writer, err := os.Pipe()
	if err != nil {
		return result, rejected
	}
	defer material.Close()
	defer writer.Close()
	cmd.ExtraFiles = []*os.File{material}
	if cmd.Start() != nil {
		return result, rejected
	}
	_ = material.Close()
	waited := false
	defer func() {
		if !waited {
			_ = cmd.Process.Kill()
			_ = cmd.Wait()
		}
	}()
	read := func() (executionprofile.Decoded, error) {
		frame, err := executionprofile.Read(runtime)
		if err == nil {
			result.Frames = append(result.Frames, frame.Frame)
		}
		return frame, err
	}
	offer, err := read()
	if err != nil || offer.Frame["type"] != "Offer" {
		return result, rejected
	}
	session := binding["session_id"]
	frame := func(kind string, n, sequence int, correlation any, body map[string]any) executionprofile.Frame {
		return executionprofile.Frame{"protocol": executionprofile.Protocol, "type": kind, "session_id": session, "message_id": id(n), "sequence": sequence, "correlation_id": correlation, "body": body}
	}
	selected := frame("Select", 101, 1, offer.Frame["message_id"], map[string]any{"protocol": executionprofile.Protocol, "capability": executionprofile.Profile, "artifact_class": "native-executable/v1"})
	prepare["sequence"] = 2
	prepare["correlation_id"] = selected["message_id"]
	if executionprofile.Write(parent, selected) != nil || executionprofile.Write(parent, prepare) != nil {
		return result, rejected
	}
	prepared, err := read()
	if err != nil || prepared.Frame["type"] != "Prepared" || !reflect.DeepEqual(prepared.Frame["body"].(map[string]any)["artifact"], artifact) {
		return result, rejected
	}
	start := frame("Start", 104, 3, prepare["message_id"], map[string]any{"prepare_message_id": prepare["message_id"], "committed_start_id": id(20), "remaining_run_ms": 9000})
	provision := frame("Provision", 105, 4, prepare["message_id"], map[string]any{"prepare_message_id": prepare["message_id"], "committed_start_id": id(20), "binding": binding, "grant_id": id(21), "delivery_id": id(22), "expires_at": time.Now().Add(9 * time.Second).UTC().Format(time.RFC3339Nano), "operations_digest": "sha256:" + digest([]byte("synthetic integration-get Fixture organization policy")), "capabilities": []string{"integration-get"}})
	if executionprofile.Write(parent, start) != nil || executionprofile.Write(parent, provision) != nil {
		return result, rejected
	}
	cancelFrame := frame("Cancel", 106, 5, nil, map[string]any{"cancel_id": id(23), "reason": "requested", "grace_ms": 100})
	if mode == "cancel-before-material" {
		if executionprofile.Write(parent, cancelFrame) != nil {
			return result, rejected
		}
	} else {
		envelope, _ := encode(map[string]any{"version": "runtime-private-delivery/v1", "provision": provision, "sdk_configuration": map[string]string{"endpoint": server.URL, "bearer": "synthetic-common-fixture", "organization_id": org, "solution_id": binding["solution_id"].(string), "test_ca_pem": ca}})
		wire := make([]byte, 4+len(envelope))
		binary.BigEndian.PutUint32(wire, uint32(len(envelope)))
		copy(wire[4:], envelope)
		if _, err := writer.Write(wire); err != nil {
			return result, rejected
		}
		_ = writer.Close()
		if mode == "cancel-running" {
			select {
			case <-entered:
			case <-ctx.Done():
				return result, rejected
			}
			if executionprofile.Write(parent, cancelFrame) != nil {
				return result, rejected
			}
		}
	}
	var reportID any
	for {
		received, err := read()
		if err != nil {
			return result, rejected
		}
		switch received.Frame["type"] {
		case "Heartbeat":
			continue
		case "Result":
			if mode == "cancel-running" || mode == "cancel-before-material" {
				return result, rejected
			}
			value := received.Frame["body"].(map[string]any)
			if value["outcome"] != "success" {
				return result, rejected
			}
			output := value["value"].(map[string]any)
			if output["ready"] != (mode != "missing") {
				return result, rejected
			}
			if mode == "missing" {
				missing := output["missing_keys"].([]any)
				if !reflect.DeepEqual(missing, []any{"alpha", "zeta"}) {
					return result, rejected
				}
			}
			result.ResultPayloadSHA256 = received.PayloadSHA256()
			reportID = received.Frame["message_id"]
			receipt := frame("ResultReceipt", 107, 5, reportID, map[string]any{"result_message_id": reportID, "result_sha256": received.PayloadSHA256(), "decision_id": id(24), "disposition": "accepted", "winner": "result"})
			if executionprofile.Write(parent, receipt) != nil {
				return result, rejected
			}
		case "Stopped":
			stop := received.Frame["body"].(map[string]any)
			if mode == "cancel-running" || mode == "cancel-before-material" {
				if stop["reason"] != "cancelled" || stop["cancel_id"] != id(23) || stop["result_message_id"] != nil {
					return result, rejected
				}
			} else if stop["reason"] != "completed" || stop["result_message_id"] != reportID {
				return result, rejected
			}
			if cmd.Wait() != nil {
				waited = true
				return result, rejected
			}
			waited = true
			result.SDKCalls = calls.Load()
			if mode == "cancel-running" {
				select {
				case <-sdkCancelled:
					result.SDKCancelled = true
				case <-ctx.Done():
					return result, rejected
				}
			}
			if (mode == "cancel-before-material" && result.SDKCalls != 0) || (mode != "cancel-before-material" && result.SDKCalls != 1) {
				return result, rejected
			}
			result.ElapsedMS = float64(time.Since(started)) / float64(time.Millisecond)
			return result, nil
		default:
			return result, rejected
		}
	}
}

func main() {
	var f fixture
	flag.StringVar(&f.adapter, "adapter", "", "compiled adapter")
	flag.StringVar(&f.workflow, "workflow", "", "unchanged compiled workflow")
	flag.StringVar(&f.evidence, "evidence", "", "retained build evidence")
	flag.StringVar(&f.graph, "graph", "", "module graph")
	flag.StringVar(&f.inputSchema, "input-schema", "", "input schema")
	flag.StringVar(&f.outputSchema, "output-schema", "", "output schema")
	flag.StringVar(&f.vectors, "vectors", "", "published common structural vectors")
	out := flag.String("output", "", "retained public evidence path")
	flag.Parse()
	var observations []observation
	failedCase := ""
	for _, mode := range []string{"success", "success-again", "missing", "cancel-before-material", "cancel-running"} {
		value, err := runCase(f, mode)
		observations = append(observations, value)
		if err != nil {
			failedCase = mode
			break
		}
	}
	raw, err := json.MarshalIndent(map[string]any{"profile": "local-common-protocol-only", "failed_case": failedCase, "rust_admission": false, "restricted_issuer": false, "durable_projection": false, "cleanup_acceptance": false, "observations": observations}, "", "  ")
	if err != nil || os.WriteFile(*out, append(raw, '\n'), 0600) != nil {
		os.Exit(1)
	}
	if failedCase != "" {
		_, _ = io.WriteString(os.Stderr, "common protocol fixture failed\n")
		os.Exit(1)
	}
}
