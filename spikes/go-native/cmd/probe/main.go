// Probe supervises an already-built artifact using synthetic local fixtures.
// It is not a Rust coordinator or a bifrost.runtime/v1 implementation.
package main

import (
	"bytes"
	"encoding/json"
	"encoding/pem"
	"errors"
	"flag"
	"fmt"
	bifrost "github.com/midtown-technology-group/bifrost-go"
	"github.com/midtown-technology-group/bifrost-go/internal/nativeadapter"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"sync/atomic"
	"syscall"
	"time"
)

type sample struct {
	StartupToSDKMS float64 `json:"startup_to_sdk_ms"`
	ExecutionMS    float64 `json:"execution_ms"`
	MaxRSSKiB      int64   `json:"max_rss_kib"`
}

func ms(d time.Duration) float64 { return float64(d) / float64(time.Millisecond) }

func launch(binary, endpoint, ca, input string) (*exec.Cmd, *bytes.Buffer, *bytes.Buffer, error) {
	r, w, err := os.Pipe()
	if err != nil {
		return nil, nil, nil, err
	}
	cmd := exec.Command(binary, "--local")
	cmd.Env = []string{"PATH=/nonexistent"}
	cmd.Stdin = bytes.NewBufferString(input)
	cmd.ExtraFiles = []*os.File{r}
	out, diagnostics := new(bytes.Buffer), new(bytes.Buffer)
	cmd.Stdout = out
	cmd.Stderr = diagnostics
	p := struct {
		bifrost.Provision
		TestCAPEM string `json:"test_ca_pem"`
	}{
		Provision: bifrost.Provision{Endpoint: endpoint, Bearer: "synthetic-fixture", OrganizationID: "org-a"}, TestCAPEM: ca}
	if err = cmd.Start(); err != nil {
		r.Close()
		w.Close()
		return nil, nil, nil, err
	}
	r.Close()
	if err = json.NewEncoder(w).Encode(p); err != nil {
		w.Close()
		cmd.Process.Kill()
		cmd.Wait()
		return nil, nil, nil, err
	}
	w.Close()
	return cmd, out, diagnostics, nil
}

func probe(binary, output string, descending bool, sealedDigest string) error {
	var materializationMS float64
	if sealedDigest != "" {
		started := time.Now()
		source, err := os.Open(binary)
		if err != nil {
			return errors.New("sealed artifact source unavailable")
		}
		sealed, err := nativeadapter.SealExecutable(source, sealedDigest)
		closeErr := source.Close()
		if err != nil {
			return err
		}
		defer sealed.Close()
		if closeErr != nil {
			return errors.New("sealed artifact source close failed")
		}
		binary = sealed.Path()
		materializationMS = ms(time.Since(started))
	}
	var first atomic.Int64
	var hold atomic.Bool
	entered := make(chan struct{}, 1)
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		first.CompareAndSwap(0, time.Now().UnixNano())
		var body struct {
			Name  string `json:"name"`
			Scope string `json:"scope"`
		}
		if r.URL.Path != "/api/sdk/integrations/get" || r.Header.Get("Authorization") != "Bearer synthetic-fixture" || json.NewDecoder(r.Body).Decode(&body) != nil || body.Name != "Fixture" || body.Scope != "org-a" {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		if hold.Load() {
			entered <- struct{}{}
			<-r.Context().Done()
			return
		}
		_, _ = w.Write([]byte(`{"integration_id":"fixture","config":{"enabled":false,"credential":"synthetic-secret"}}`))
	}))
	defer s.Close()
	ca := string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: s.Certificate().Raw}))
	input := `{"integration_name":"Fixture","required_keys":["credential","enabled"]}`
	samples := make([]sample, 0, 20)
	for i := 0; i < 20; i++ {
		first.Store(0)
		started := time.Now()
		cmd, out, diagnostics, err := launch(binary, s.URL, ca, input)
		if err != nil {
			return err
		}
		if err = cmd.Wait(); err != nil {
			return errors.New("local workflow failed: " + diagnostics.String())
		}
		var result struct {
			Ready   bool     `json:"ready"`
			Missing []string `json:"missing_keys"`
		}
		if json.Unmarshal(out.Bytes(), &result) != nil || !result.Ready || len(result.Missing) != 0 || bytes.Contains(out.Bytes(), []byte("synthetic-secret")) || first.Load() == 0 {
			return errors.New("local result/SDK evidence mismatch")
		}
		samples = append(samples, sample{StartupToSDKMS: ms(time.Unix(0, first.Load()).Sub(started)), ExecutionMS: ms(time.Since(started)), MaxRSSKiB: cmd.ProcessState.SysUsage().(*syscall.Rusage).Maxrss})
	}
	// Exercise the branch actually changed by the warm edit. The successful
	// readiness samples above have no missing keys and alone cannot observe it.
	missingInput := `{"integration_name":"Fixture","required_keys":[" zeta ","alpha","zeta","","credential"]}`
	branch, branchOut, _, err := launch(binary, s.URL, ca, missingInput)
	if err != nil {
		return err
	}
	if err = branch.Wait(); err != nil {
		return errors.New("edited behavior fixture failed")
	}
	var branchResult struct {
		Ready   bool     `json:"ready"`
		Missing []string `json:"missing_keys"`
	}
	expected := []string{"alpha", "zeta"}
	if descending {
		expected = []string{"zeta", "alpha"}
	}
	if json.Unmarshal(branchOut.Bytes(), &branchResult) != nil || branchResult.Ready || len(branchResult.Missing) != 2 || branchResult.Missing[0] != expected[0] || branchResult.Missing[1] != expected[1] || bytes.Contains(branchOut.Bytes(), []byte("synthetic-secret")) {
		return errors.New("source edit did not produce expected changed output")
	}
	hold.Store(true)
	cmd, _, _, err := launch(binary, s.URL, ca, input)
	if err != nil {
		return err
	}
	select {
	case <-entered:
	case <-time.After(10 * time.Second):
		cmd.Process.Kill()
		cmd.Wait()
		return errors.New("cancellation fixture was not reached")
	}
	started := time.Now()
	if err = cmd.Process.Signal(syscall.SIGTERM); err != nil {
		return err
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case err = <-done:
		if err == nil {
			return errors.New("cancelled workflow reported success")
		}
	case <-time.After(3 * time.Second):
		cmd.Process.Kill()
		<-done
		return errors.New("cooperative cancellation exceeded local deadline")
	}
	cancelMS := ms(time.Since(started))
	data := map[string]any{"profile": "local-authoring-only", "sdk_version": bifrost.SDKVersion, "samples": samples, "cancellation_ms": cancelMS, "same_artifact_runs": 22, "missing_keys_observation": branchResult.Missing, "runtime_compiler_available": false, "rust_admission": false, "durable_projection": false}
	if sealedDigest != "" {
		data["sealed_executable_sha256"] = sealedDigest
		data["sealed_materialization_ms"] = materializationMS
	}
	b, err := json.MarshalIndent(data, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(output, append(b, '\n'), 0644)
}
func main() {
	binary := flag.String("artifact", "", "already built absolute artifact path")
	output := flag.String("output", "", "measurement JSON path")
	descending := flag.Bool("descending", false, "expect the behavior-changing warm source edit")
	sealedDigest := flag.String("sealed-artifact-sha256", "", "verify and execute sealed bytes using the supplied accepted binary digest; local fixture only")
	flag.Parse()
	if !filepath.IsAbs(*binary) || *output == "" {
		fmt.Fprintln(os.Stderr, "artifact and output are required")
		os.Exit(1)
	}
	if err := probe(*binary, *output, *descending, *sealedDigest); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
