//go:build linux

package nativeadapter

import (
	"os"
	"syscall"
	"time"

	"github.com/midtown-technology-group/bifrost-go/executionprofile"
)

// ReadInherited consumes and closes the adapter's inherited read-only pipe.
// deadline must be the current preparation budget, never supplied by the tenant.
// The caller must still establish that its verified guardian owns the writer;
// kernel descriptor shape alone is not authenticated issuer custody.
func (r *DeliveryReader) ReadInherited(fd uintptr, expected executionprofile.Decoded, deadline time.Time) (SDKConfiguration, error) {
	if fd < 3 {
		return SDKConfiguration{}, DeliveryRejected
	}
	// Ownership transfers at entry. Validate the raw descriptor before wrapping
	// it so the Go poller sees nonblocking mode and can enforce the read deadline.
	ownedRaw := true
	defer func() {
		if ownedRaw {
			_ = syscall.Close(int(fd))
		}
	}()
	body, ok := expected.Frame["body"].(map[string]any)
	expiryText, expiryOK := body["expires_at"].(string)
	expires, expiryErr := time.Parse(time.RFC3339Nano, expiryText)
	if !ok || !expiryOK || expiryErr != nil {
		return SDKConfiguration{}, DeliveryRejected
	}
	if expires.Before(deadline) {
		deadline = expires
	}
	flags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, fd, syscall.F_GETFL, 0)
	if errno != 0 || flags&syscall.O_ACCMODE != syscall.O_RDONLY {
		return SDKConfiguration{}, DeliveryRejected
	}
	var stat syscall.Stat_t
	if syscall.Fstat(int(fd), &stat) != nil || stat.Mode&syscall.S_IFMT != syscall.S_IFIFO {
		return SDKConfiguration{}, DeliveryRejected
	}
	// Mark private material close-on-exec before reading anything; unrelated or
	// tenant children must never inherit it. Preserve any existing descriptor flags.
	descriptorFlags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, fd, syscall.F_GETFD, 0)
	if errno != 0 {
		return SDKConfiguration{}, DeliveryRejected
	}
	if _, _, errno = syscall.Syscall(syscall.SYS_FCNTL, fd, syscall.F_SETFD, descriptorFlags|syscall.FD_CLOEXEC); errno != 0 {
		return SDKConfiguration{}, DeliveryRejected
	}
	if syscall.SetNonblock(int(fd), true) != nil {
		return SDKConfiguration{}, DeliveryRejected
	}
	file := os.NewFile(fd, "private-runtime-delivery")
	if file == nil {
		return SDKConfiguration{}, DeliveryRejected
	}
	ownedRaw = false
	defer file.Close()
	if !deadline.After(time.Now()) || file.SetReadDeadline(deadline) != nil {
		return SDKConfiguration{}, DeliveryRejected
	}
	sdk, err := r.Read(file, expected, time.Now())
	if err != nil || !deadline.After(time.Now()) {
		return SDKConfiguration{}, DeliveryRejected
	}
	return sdk, nil
}
