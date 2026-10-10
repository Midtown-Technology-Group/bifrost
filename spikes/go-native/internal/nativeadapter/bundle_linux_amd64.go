//go:build linux && amd64

package nativeadapter

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
)

var BundleRejected = errors.New("native bundle rejected")

// BundleReaders are already-open objects from the guardian's artifact store.
// No path, network fetch, compiler, application hook or credential is accepted.
// Acceptance of the descriptor and producer evidence belongs to the owner.
type BundleReaders struct {
	Child, Adapter, BuildEvidence, ModuleGraph, InputSchema, OutputSchema io.Reader
}

// VerifiedBundle retains private byte snapshots, independent of mutable paths
// and caller maps. Verification proves byte identity, not deployment acceptance,
// issuer custody, source closure, or permission to execute.
type VerifiedBundle struct {
	artifact                                                              map[string]any
	child, adapter, buildEvidence, moduleGraph, inputSchema, outputSchema []byte
}

func bundleObject(reader io.Reader, limit int64, digest string) ([]byte, error) {
	if reader == nil || len(digest) != 64 {
		return nil, BundleRejected
	}
	raw, err := io.ReadAll(io.LimitReader(reader, limit+1))
	if err != nil || len(raw) == 0 || int64(len(raw)) > limit || bytesDigest(raw) != digest {
		return nil, BundleRejected
	}
	return raw, nil
}

// VerifyBundle checks all descriptor-addressed objects and separately pinned
// schemas. Schema pins must come from accepted registration, never tenant input.
// Common artifact schema/identity and ELF validation still occur in PrepareNative.
func VerifyBundle(artifact map[string]any, inputDigest, outputDigest string, readers BundleReaders) (*VerifiedBundle, error) {
	raw, err := json.Marshal(artifact)
	if err != nil || len(raw) > 65536 || !strictJSONDepth(raw, 64) {
		return nil, BundleRejected
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var retained map[string]any
	if decoder.Decode(&retained) != nil || retained["kind"] != "native-executable/v1" || retained["runtime_protocol"] != "bifrost.runtime/v1" {
		return nil, BundleRejected
	}
	dependencies, ok := retained["dependencies"].(map[string]any)
	if !ok || dependencies["kind"] != "go-module-graph/v1" {
		return nil, BundleRejected
	}
	graphDigest, ok := dependencies["digest"].(string)
	if !ok || len(graphDigest) != 71 || graphDigest[:7] != "sha256:" {
		return nil, BundleRejected
	}
	childDigest, childOK := retained["executable_sha256"].(string)
	adapterDigest, adapterOK := retained["adapter_sha256"].(string)
	evidenceDigest, evidenceOK := retained["build_evidence_sha256"].(string)
	if !childOK || !adapterOK || !evidenceOK {
		return nil, BundleRejected
	}
	bundle := &VerifiedBundle{artifact: retained}
	objects := []struct {
		reader      io.Reader
		limit       int64
		digest      string
		destination *[]byte
	}{
		{readers.Child, MaxExecutableBytes, childDigest, &bundle.child},
		{readers.Adapter, MaxExecutableBytes, adapterDigest, &bundle.adapter},
		{readers.BuildEvidence, 2 * 1024 * 1024, evidenceDigest, &bundle.buildEvidence},
		{readers.ModuleGraph, 2 * 1024 * 1024, graphDigest[7:], &bundle.moduleGraph},
		{readers.InputSchema, 65536, inputDigest, &bundle.inputSchema},
		{readers.OutputSchema, 65536, outputDigest, &bundle.outputSchema},
	}
	for _, object := range objects {
		value, err := bundleObject(object.reader, object.limit, object.digest)
		if err != nil {
			return nil, err
		}
		*object.destination = value
	}
	if !strictJSONDepth(bundle.buildEvidence, 64) {
		return nil, BundleRejected
	}
	var evidence map[string]json.RawMessage
	if json.Unmarshal(bundle.buildEvidence, &evidence) != nil || evidence == nil {
		return nil, BundleRejected
	}
	if _, ok := parseWorkloadSchema(bundle.inputSchema); !ok {
		return nil, BundleRejected
	}
	if _, ok := parseWorkloadSchema(bundle.outputSchema); !ok {
		return nil, BundleRejected
	}
	return bundle, nil
}

// PreparationInputs creates fresh readers for another execution of the same
// bytes. It neither rebuilds nor reopens storage. Each Prepare still checks its
// independent binding and seals a process-local executable handle.
func (b *VerifiedBundle) PreparationInputs(binding map[string]any) PreparationInputs {
	return PreparationInputs{
		Binding:  cloneProtocolValue(binding).(map[string]any),
		Artifact: cloneProtocolValue(b.artifact).(map[string]any),
		Child:    bytes.NewReader(b.child), Adapter: bytes.NewReader(b.adapter),
		InputSchema: append([]byte(nil), b.inputSchema...), OutputSchema: append([]byte(nil), b.outputSchema...),
	}
}
