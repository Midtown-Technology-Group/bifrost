//go:build linux && amd64

package main

import (
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
)

func TestInvalidInvocationCannotReachMaterialOrAdapter(t *testing.T) {
	for _, args := range [][]string{nil, {"--bundle", "/tmp"}, {"--index-sha256", strings.Repeat("A", 64)}, {"--index-sha256", strings.Repeat("a", 63)}, {"--index-sha256", strings.Repeat("a", 64), "extra"}} {
		if run(args) == nil {
			t.Fatal("invalid invocation was accepted")
		}
	}
}

func TestPrivateFIFOKernelIdentityAndReadonlyAccess(t *testing.T) {
	path := filepath.Join(t.TempDir(), "material")
	if err := syscall.Mkfifo(path, 0600); err != nil {
		t.Fatal(err)
	}
	file, err := openMaterial(path)
	if err != nil {
		t.Fatal(err) // supported build lane must retain its nonroot identity
	}
	defer file.Close()
	fd := file.Fd()
	flags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, fd, syscall.F_GETFL, 0)
	if errno != 0 || flags&syscall.O_ACCMODE != syscall.O_RDONLY {
		t.Fatal("private descriptor was not readonly")
	}
	// A reader may open before the guardian has supplied material. No SDK data
	// is synthesized and the launcher must not create a writer/credential.
	info, err := os.Stat(path)
	if err != nil || info.Mode()&os.ModeNamedPipe == 0 {
		t.Fatal("FIFO custody was lost")
	}
}

func TestRegularSymlinkAndSharedModeAreDenied(t *testing.T) {
	base := t.TempDir()
	regular := filepath.Join(base, "regular")
	if err := os.WriteFile(regular, []byte("not SDK material"), 0600); err != nil {
		t.Fatal(err)
	}
	fifo := filepath.Join(base, "fifo")
	if err := syscall.Mkfifo(fifo, 0640); err != nil {
		t.Fatal(err)
	}
	// Set exact shared bits independently of the build lane's umask.
	if err := os.Chmod(fifo, 0640); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(base, "link")
	if err := os.Symlink(fifo, link); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{regular, fifo, link} {
		if file, err := openMaterial(path); err == nil {
			file.Close()
			t.Fatal("invalid private material object accepted")
		}
	}
}
