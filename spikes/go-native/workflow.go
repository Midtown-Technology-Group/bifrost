package bifrost

import (
	"context"
	"crypto/x509"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/signal"
	"syscall"

	"github.com/midtown-technology-group/bifrost-go/internal/authoring"
)

type capabilityClientKey struct{}

// ClientFromContext returns the capabilities supplied by the local runtime.
// It does not derive admission, caller scope or credentials from workflow input.
func ClientFromContext(ctx context.Context) (*Client, error) {
	client, ok := ctx.Value(capabilityClientKey{}).(*Client)
	if !ok || client == nil {
		return nil, errors.New("runtime capabilities unavailable")
	}
	return client, nil
}

func localSetup(ctx context.Context, reader io.Reader) (context.Context, func(), error) {
	var provision struct {
		Provision
		TestCAPEM string `json:"test_ca_pem"`
	}
	decoder := json.NewDecoder(reader)
	decoder.DisallowUnknownFields()
	if decoder.Decode(&provision) != nil {
		return nil, nil, errors.New("invalid supervisor provision")
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		return nil, nil, errors.New("trailing supervisor provision")
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM([]byte(provision.TestCAPEM)) {
		return nil, nil, errors.New("missing local test CA")
	}
	client, err := NewClient(provision.Provision, roots)
	if err != nil {
		return nil, nil, err
	}
	return context.WithValue(ctx, capabilityClientKey{}, client), client.Close, nil
}

// Workflow adapts an ordinary typed handler to the explicitly local authoring
// runner. Generic arguments are inferred; user control flow stays in Go.
// It is not yet bifrost.runtime/v1. Package initialization has already happened
// before main: a trusted adapter must gate launch under the shared contract.
func Workflow[Input, Output any](handler func(context.Context, Input) (Output, error)) {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	provision := os.NewFile(3, "provision")
	if provision == nil {
		_, _ = io.WriteString(os.Stderr, "workflow failed\n")
		os.Exit(1)
	}
	defer provision.Close()
	if err := authoring.Run(ctx, os.Args[1:], os.Stdin, os.Stdout, provision, localSetup, handler); err != nil {
		// Arbitrary application errors can contain sensitive data. The local CLI
		// prints a static failure; ordinary unit tests still receive Run's error.
		_, _ = io.WriteString(os.Stderr, "workflow failed\n")
		os.Exit(1)
	}
}
