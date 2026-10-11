//go:build linux && amd64

// Trusted minimal container init. It adds the private inherited descriptor and
// forwards standard streams; admission and lifecycle remain with the coordinator.
package main

import (
	"context"
	"errors"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"syscall"
	"time"
)

var rejected = errors.New("native launcher rejected")

func indexDigest(args []string) (string, error) {
	if (len(args) != 2 && len(args) != 3) || args[0] != "--index-sha256" || len(args[1]) != 64 || (len(args) == 3 && args[2] != "--sdk-relay") {
		return "", rejected
	}
	for _, ch := range args[1] {
		if !(ch >= '0' && ch <= '9' || ch >= 'a' && ch <= 'f') {
			return "", rejected
		}
	}
	return args[1], nil
}

func openMaterial(path string) (*os.File, error) {
	if os.Geteuid() == 0 {
		return nil, rejected
	}
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_NONBLOCK|syscall.O_CLOEXEC|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return nil, rejected
	}
	file := os.NewFile(uintptr(fd), "private-material")
	if file == nil {
		_ = syscall.Close(fd)
		return nil, rejected
	}
	var stat syscall.Stat_t
	if syscall.Fstat(fd, &stat) != nil || stat.Mode&syscall.S_IFMT != syscall.S_IFIFO || stat.Mode&0777 != 0600 || stat.Uid != uint32(os.Geteuid()) {
		_ = file.Close()
		return nil, rejected
	}
	return file, nil
}

func run(args []string) error {
	digest, err := indexDigest(args)
	if err != nil {
		return err
	}
	// The guardian stages this one private FIFO and keeps its writer alive. No
	// material may be written until authenticated issuance and observed Rust
	// release. Opening a FIFO or matching an index is never that authority.
	material, err := openMaterial("/material/sdk.pipe")
	if err != nil {
		return err
	}
	defer material.Close()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if len(args) == 3 {
		relay, err := startSDKRelay(ctx, "/sdk/ingress.sock", "127.0.0.1:8443")
		if err != nil {
			return err
		}
		defer relay.close()
	}
	cmd := exec.CommandContext(ctx, "/adapter", "--bundle", "/bundle", "--index-sha256", digest)
	cmd.Env = []string{"PATH=/nonexistent", "HOME=/nonexistent"}
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	cmd.ExtraFiles = []*os.File{material} // fixed child FD3, readonly FIFO
	cmd.Cancel = func() error { return cmd.Process.Signal(syscall.SIGTERM) }
	cmd.WaitDelay = 250 * time.Millisecond
	return cmd.Run()
}

func main() {
	if run(os.Args[1:]) != nil {
		_, _ = io.WriteString(os.Stderr, "native launcher failed\n")
		os.Exit(1)
	}
}
