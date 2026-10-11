//go:build linux && amd64

package main

import (
	"context"
	"io"
	"net"
	"os"
	"sync"
	"syscall"
	"time"
)

// The relay has one fixed destination and forwards opaque TLS bytes. It holds
// no certificate key, bearer, application credential or lifecycle authority.
// It cannot choose an Internet/DB destination or terminate the SDK's TLS.
const relayByteLimit = 2 << 20
const relayConnectionLimit = 4

type sdkRelay struct {
	listener net.Listener
	cancel   context.CancelFunc
	done     chan struct{}
}

func privateSDKSocket(path string) (uint64, uint64, error) {
	info, err := os.Lstat(path)
	if err != nil || info.Mode()&os.ModeSocket == 0 || info.Mode().Perm() != 0600 || os.Geteuid() == 0 {
		return 0, 0, rejected
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != uint32(os.Geteuid()) {
		return 0, 0, rejected
	}
	return uint64(stat.Dev), stat.Ino, nil
}

func startSDKRelay(parent context.Context, socketPath, bindAddress string) (*sdkRelay, error) {
	device, inode, err := privateSDKSocket(socketPath)
	if err != nil {
		return nil, err
	}
	address, err := net.ResolveTCPAddr("tcp4", bindAddress)
	if err != nil || !address.IP.Equal(net.IPv4(127, 0, 0, 1)) {
		return nil, rejected
	}
	listener, err := net.ListenTCP("tcp4", address)
	if err != nil {
		return nil, rejected
	}
	ctx, cancel := context.WithCancel(parent)
	relay := &sdkRelay{listener: listener, cancel: cancel, done: make(chan struct{})}
	go func() {
		<-ctx.Done()
		_ = listener.Close()
	}()
	go func() {
		defer close(relay.done)
		defer cancel()
		var connections sync.WaitGroup
		defer connections.Wait()
		for count := 0; count < relayConnectionLimit; count++ {
			client, err := listener.AcceptTCP()
			if err != nil {
				return
			}
			// Parent owns an immutable, per-session socket directory. Replacing
			// the original inode fails closed; there is no reconnect fallback.
			currentDevice, currentInode, err := privateSDKSocket(socketPath)
			if err != nil || currentDevice != device || currentInode != inode {
				_ = client.Close()
				return
			}
			connections.Add(1)
			go func() {
				defer connections.Done()
				forwardSDK(ctx, client, socketPath)
			}()
		}
		// Bounded accepted connections; no further tenant socket is serviced.
		_ = listener.Close()
	}()
	return relay, nil
}

func forwardSDK(ctx context.Context, client net.Conn, socketPath string) {
	defer client.Close()
	dialer := net.Dialer{Timeout: time.Second}
	upstream, err := dialer.DialContext(ctx, "unix", socketPath)
	if err != nil {
		return
	}
	defer upstream.Close()
	deadline := time.Now().Add(10 * time.Second)
	if client.SetDeadline(deadline) != nil || upstream.SetDeadline(deadline) != nil {
		return
	}
	finished := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			_ = client.Close()
			_ = upstream.Close()
		case <-finished:
		}
	}()
	defer close(finished)
	directions := make(chan struct{}, 2)
	copyBounded := func(destination, source net.Conn) {
		_, _ = io.Copy(destination, io.LimitReader(source, relayByteLimit))
		// EOF/error/budget exhaustion always closes both directions; a
		// partially transferred request is never replayed by this relay.
		_ = client.Close()
		_ = upstream.Close()
		directions <- struct{}{}
	}
	go copyBounded(upstream, client)
	go copyBounded(client, upstream)
	<-directions
	<-directions
}

func (relay *sdkRelay) close() {
	relay.cancel()
	_ = relay.listener.Close()
	<-relay.done
}
