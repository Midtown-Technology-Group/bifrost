//go:build linux && amd64

package nativeadapter

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"runtime"
	"strconv"
	"syscall"
	"unsafe"
)

const MaxExecutableBytes = 32 * 1024 * 1024

var ArtifactRejected = errors.New("native executable bytes rejected")

// SealedExecutable is an immutable kernel-held copy of the accepted child bytes.
// No tenant code runs during materialization. The accepted descriptor/builder
// evidence and owner admission must be verified separately by the guardian.
type SealedExecutable struct{ file *os.File }

// SealExecutable eliminates path replacement and in-place write races between
// digest verification and a later authorized exec. Linux/amd64 is the initial
// supported target. Unsupported memfd/sealing policy fails closed.
func SealExecutable(source io.Reader, expectedSHA256 string) (*SealedExecutable, error) {
	digestBytes, err := hex.DecodeString(expectedSHA256)
	if err != nil || len(digestBytes) != sha256.Size || hex.EncodeToString(digestBytes) != expectedSHA256 {
		return nil, ArtifactRejected
	}
	name := []byte("bifrost-accepted-workflow\x00")
	// Linux amd64 syscall 319, MFD_CLOEXEC | MFD_ALLOW_SEALING. No CGO or
	// external launcher dependency; the Go module graph remains unchanged.
	fd, _, errno := syscall.Syscall(319, uintptr(unsafe.Pointer(&name[0])), 3, 0)
	runtime.KeepAlive(name)
	if errno != 0 {
		return nil, ArtifactRejected
	}
	file := os.NewFile(fd, "sealed-native-workflow")
	if file == nil {
		_ = syscall.Close(int(fd))
		return nil, ArtifactRejected
	}
	accepted := false
	defer func() {
		if !accepted {
			_ = file.Close()
		}
	}()
	hash := sha256.New()
	size, err := io.Copy(io.MultiWriter(file, hash), io.LimitReader(source, MaxExecutableBytes+1))
	if err != nil || size == 0 || size > MaxExecutableBytes || hex.EncodeToString(hash.Sum(nil)) != expectedSHA256 || file.Chmod(0500) != nil {
		return nil, ArtifactRejected
	}
	// F_ADD_SEALS=1033; SEAL_SEAL|SHRINK|GROW|WRITE=15. Seals cannot be
	// removed, and this final seal prohibits adding or changing the policy.
	if _, _, errno = syscall.Syscall(syscall.SYS_FCNTL, fd, 1033, 15); errno != 0 {
		return nil, ArtifactRejected
	}
	seals, _, errno := syscall.Syscall(syscall.SYS_FCNTL, fd, 1034, 0)
	if errno != 0 || seals&15 != 15 {
		return nil, ArtifactRejected
	}
	if _, err = file.Seek(0, io.SeekStart); err != nil {
		return nil, ArtifactRejected
	}
	accepted = true
	return &SealedExecutable{file: file}, nil
}

// Path identifies these sealed bytes, never the original mutable source path.
// The guardian retains the handle until the single authorized launch resolves.
// This path is process-local and carries no portable launch authority.
func (e *SealedExecutable) Path() string {
	return "/proc/self/fd/" + strconv.FormatUint(uint64(e.file.Fd()), 10)
}
func (e *SealedExecutable) Close() error { return e.file.Close() }
