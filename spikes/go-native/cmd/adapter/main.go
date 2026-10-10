//go:build linux && amd64

// Adapter speaks only the common runtime contract and launches an already-built
// child. Deployment acceptance, release and durable lifecycle belong to the owner.
package main

import (
	"context"
	"flag"
	"io"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/midtown-technology-group/bifrost-go/internal/nativeadapter"
)

func run() error {
	directory := flag.String("bundle", "", "guardian-staged read-only session bundle")
	digest := flag.String("index-sha256", "", "guardian-pinned session delivery index SHA256")
	flag.Parse()
	if flag.NArg() != 0 || *directory == "" || *digest == "" {
		return nativeadapter.BundleRejected
	}
	bundle, binding, err := nativeadapter.LoadSessionBundle(*directory, *digest)
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	return nativeadapter.RunInheritedSession(ctx, nativeadapter.SessionTransport{Parent: os.Stdin, Runtime: os.Stdout, MaterialFD: 3}, bundle.PreparationInputs(binding))
}

func main() {
	if run() != nil {
		_, _ = io.WriteString(os.Stderr, "native adapter failed\n")
		os.Exit(1)
	}
}
