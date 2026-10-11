//go:build linux && amd64

package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func privateListener(t *testing.T, path string) net.Listener {
	t.Helper()
	listener, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path, 0600); err != nil {
		listener.Close()
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = listener.Close() })
	return listener
}

func TestRelayPreservesVerifiedHTTPSWithoutCertificateOrBearerAccess(t *testing.T) {
	// The real TLS server is outside the relay; the relay only carries bytes.
	certificateServer := httptest.NewTLSServer(http.NotFoundHandler())
	defer certificateServer.Close()
	path := filepath.Join(t.TempDir(), "ingress.sock")
	listener := privateListener(t, path)
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api/sdk/integrations/get" {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		_, _ = io.WriteString(w, `{"config":{"ready":true}}`)
	}), ReadHeaderTimeout: time.Second}
	defer server.Close()
	go func() { _ = server.Serve(tls.NewListener(listener, certificateServer.TLS.Clone())) }()
	relay, err := startSDKRelay(context.Background(), path, "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer relay.close()
	roots := x509.NewCertPool()
	roots.AddCert(certificateServer.Certificate())
	transport := &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: roots}}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: time.Second}
	response, err := client.Post("https://"+relay.listener.Addr().String()+"/api/sdk/integrations/get", "application/json", strings.NewReader(`{"name":"Fixture"}`))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil || response.StatusCode != http.StatusOK || string(body) != `{"config":{"ready":true}}` {
		t.Fatal("verified HTTPS response did not survive opaque transport")
	}
}

func TestRelayCancellationClosesAnInFlightConnection(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ingress.sock")
	listener := privateListener(t, path)
	if err := listener.(*net.UnixListener).SetDeadline(time.Now().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	relay, err := startSDKRelay(ctx, path, "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer relay.close()
	client, err := net.Dial("tcp4", relay.listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	upstream, err := listener.Accept()
	if err != nil {
		t.Fatal(err)
	}
	defer upstream.Close()
	cancel()
	if err := client.SetReadDeadline(time.Now().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	var byte [1]byte
	_, err = client.Read(byte[:])
	if err == nil {
		t.Fatal("cancel left the SDK connection open")
	}
	if timeout, ok := err.(net.Error); ok && timeout.Timeout() {
		t.Fatal("cancel did not close the connection before the read deadline")
	}
}

func TestRelayCannotReconnectToAReplacementSocket(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ingress.sock")
	_ = privateListener(t, path)
	relay, err := startSDKRelay(context.Background(), path, "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer relay.close()
	// Retain the original listener while unlinking its pathname so its inode
	// cannot be recycled into the replacement during this test.
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	replacement := privateListener(t, path)
	if err := replacement.(*net.UnixListener).SetDeadline(time.Now().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	client, err := net.Dial("tcp4", relay.listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	_ = client.SetReadDeadline(time.Now().Add(time.Second))
	var byte [1]byte
	if _, err := client.Read(byte[:]); err == nil {
		t.Fatal("replacement socket served the old relay")
	}
	if connection, err := replacement.Accept(); err == nil {
		connection.Close()
		t.Fatal("relay connected to replacement ingress")
	}
}

func TestRelayRejectsSharedSymlinkRegularAndNonLoopbackSockets(t *testing.T) {
	base := t.TempDir()
	path := filepath.Join(base, "ingress.sock")
	_ = privateListener(t, path)
	regular := filepath.Join(base, "regular")
	if err := os.WriteFile(regular, nil, 0600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(base, "link")
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	for _, invalid := range []string{regular, link} {
		if relay, err := startSDKRelay(context.Background(), invalid, "127.0.0.1:0"); err == nil {
			relay.close()
			t.Fatal("non-private socket was accepted")
		}
	}
	if relay, err := startSDKRelay(context.Background(), path, "0.0.0.0:0"); err == nil {
		relay.close()
		t.Fatal("non-loopback listener was accepted")
	}
	if err := os.Chmod(path, 0660); err != nil {
		t.Fatal(err)
	}
	if relay, err := startSDKRelay(context.Background(), path, "127.0.0.1:0"); err == nil {
		relay.close()
		t.Fatal("shared socket was accepted")
	}
}
