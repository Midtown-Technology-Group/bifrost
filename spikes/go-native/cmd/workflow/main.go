// This entrypoint is a local authoring proof, not bifrost.runtime/v1.
// The local harness starts it only when it intends computation to begin.
package main

import (
	"context"
	"crypto/x509"
	"encoding/json"
	"errors"
	"flag"
	bifrost "github.com/midtown-technology-group/bifrost-go"
	"github.com/midtown-technology-group/bifrost-go/internal/readiness"
	"io"
	"os"
	"os/signal"
	"syscall"
)

func run(ctx context.Context) error {
	local := flag.Bool("local", false, "explicit local spike mode (no Rust admission)")
	flag.Parse()
	if !*local {
		return errors.New("only explicit local spike execution is supported")
	}
	provisionFD := os.NewFile(3, "provision")
	if provisionFD == nil {
		return errors.New("missing supervisor provision")
	}
	defer provisionFD.Close()
	var provision struct {
		bifrost.Provision
		TestCAPEM string `json:"test_ca_pem"`
	}
	if json.NewDecoder(io.LimitReader(provisionFD, 65536)).Decode(&provision) != nil {
		return errors.New("invalid supervisor provision")
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM([]byte(provision.TestCAPEM)) {
		return errors.New("missing local test CA")
	}
	client, err := bifrost.NewClient(provision.Provision, roots)
	if err != nil {
		return err
	}
	defer client.Close()
	var in readiness.Input
	d := json.NewDecoder(io.LimitReader(os.Stdin, 65536))
	d.DisallowUnknownFields()
	if d.Decode(&in) != nil {
		return errors.New("invalid workflow input")
	}
	var extra any
	if d.Decode(&extra) != io.EOF {
		return errors.New("trailing workflow input")
	}
	out, err := readiness.Run(ctx, in, client.Integrations)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(out)
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := run(ctx); err != nil {
		// Capability error bodies and credential values never enter diagnostics.
		_, _ = io.WriteString(os.Stderr, "workflow failed: "+err.Error()+"\n")
		os.Exit(1)
	}
}
