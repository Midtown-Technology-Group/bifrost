//go:build linux

package nativeadapter

import (
	"bytes"
	"encoding/json"
	"os"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/midtown-technology-group/bifrost-go/executionprofile"
)

func transferDescriptor(t *testing.T, file *os.File) uintptr {
	t.Helper()
	fd, err := syscall.Dup(int(file.Fd()))
	if err != nil {
		t.Fatal(err)
	}
	if err = file.Close(); err != nil {
		_ = syscall.Close(fd)
		t.Fatal(err)
	}
	return uintptr(fd)
}

func TestInheritedReadOnlyPipe(t *testing.T) {
	raw, _, _ := deliveryFixture(t)
	raw = bytes.Replace(raw, []byte("2026-10-10T20:00:00Z"), []byte("2099-10-10T20:00:00Z"), 1)
	var envelope map[string]json.RawMessage
	if err := json.Unmarshal(raw, &envelope); err != nil {
		t.Fatal(err)
	}
	expected, err := executionprofile.Decode(envelope["provision"])
	if err != nil {
		t.Fatal(err)
	}
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	fd := transferDescriptor(t, reader)
	done := make(chan error, 1)
	go func() {
		_, err := writer.Write(deliveryWire(raw))
		closeErr := writer.Close()
		if err == nil {
			err = closeErr
		}
		done <- err
	}()
	sdk, err := (&DeliveryReader{}).ReadInherited(fd, expected, time.Now().Add(time.Second))
	if err != nil || !strings.HasPrefix(sdk.Endpoint, "https://") {
		t.Fatal("valid inherited pipe rejected")
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	var stat syscall.Stat_t
	if syscall.Fstat(int(fd), &stat) != syscall.EBADF {
		t.Fatal("private descriptor retained after success")
	}
}

func TestInheritedRejectsRegularFile(t *testing.T) {
	_, expected, _ := deliveryFixture(t)
	file, err := os.CreateTemp(t.TempDir(), "not-private-pipe")
	if err != nil {
		t.Fatal(err)
	}
	fd := transferDescriptor(t, file)
	if _, err := (&DeliveryReader{}).ReadInherited(fd, expected, time.Now().Add(time.Second)); err != DeliveryRejected {
		t.Fatal("regular file accepted")
	}
}

func TestInheritedRejectsWriter(t *testing.T) {
	_, expected, _ := deliveryFixture(t)
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	fd := transferDescriptor(t, writer)
	if _, err := (&DeliveryReader{}).ReadInherited(fd, expected, time.Now().Add(time.Second)); err != DeliveryRejected {
		t.Fatal("writable material channel accepted")
	}
}

func TestInheritedTimeoutClosesDescriptor(t *testing.T) {
	_, expected, _ := deliveryFixture(t)
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	fd := transferDescriptor(t, reader)
	deadline := time.Now().Add(20 * time.Millisecond)
	if _, err := (&DeliveryReader{}).ReadInherited(fd, expected, deadline); err != DeliveryRejected {
		t.Fatal("stalled writer accepted")
	}
	if time.Now().Before(deadline) {
		t.Fatal("pipe deadline was not exercised")
	}
	var stat syscall.Stat_t
	if syscall.Fstat(int(fd), &stat) != syscall.EBADF {
		t.Fatal("private descriptor retained after timeout")
	}
}
