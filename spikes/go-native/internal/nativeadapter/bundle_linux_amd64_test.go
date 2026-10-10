//go:build linux && amd64

package nativeadapter

import (
	"bytes"
	"io"
	"testing"
)

func bundleFixture(t *testing.T) (map[string]any, string, string, BundleReaders, [][]byte) {
	t.Helper()
	_, accepted, _ := preparationFixture(t)
	child, err := io.ReadAll(accepted.Child)
	if err != nil {
		t.Fatal(err)
	}
	adapter, err := io.ReadAll(accepted.Adapter)
	if err != nil {
		t.Fatal(err)
	}
	objects := [][]byte{child, adapter, []byte(`{"producer":"synthetic-test-not-accepted"}`), []byte("synthetic module graph\n"), accepted.InputSchema, accepted.OutputSchema}
	artifact := accepted.Artifact
	artifact["build_evidence_sha256"] = bytesDigest(objects[2])
	artifact["dependencies"].(map[string]any)["kind"] = "go-module-graph/v1"
	artifact["dependencies"].(map[string]any)["digest"] = "sha256:" + bytesDigest(objects[3])
	return artifact, bytesDigest(objects[4]), bytesDigest(objects[5]), bundleReaders(objects), objects
}

func bundleReaders(objects [][]byte) BundleReaders {
	return BundleReaders{Child: bytes.NewReader(objects[0]), Adapter: bytes.NewReader(objects[1]), BuildEvidence: bytes.NewReader(objects[2]), ModuleGraph: bytes.NewReader(objects[3]), InputSchema: bytes.NewReader(objects[4]), OutputSchema: bytes.NewReader(objects[5])}
}

func TestBundleRetainsBytesAcrossStorageReplacementAndRepeatedPreparation(t *testing.T) {
	artifact, input, output, readers, objects := bundleFixture(t)
	bundle, err := VerifyBundle(artifact, input, output, readers)
	if err != nil {
		t.Fatal(err)
	}
	_, accepted, _ := preparationFixture(t)
	expected := make([][]byte, len(objects))
	for index, raw := range objects {
		expected[index] = append([]byte(nil), raw...)
		for i := range raw {
			raw[i] = 0
		}
	}
	artifact["executable_sha256"] = "caller changed descriptor"
	first := bundle.PreparationInputs(accepted.Binding)
	first.Artifact["adapter_sha256"] = "caller changed returned descriptor"
	first.InputSchema[0] = '!'
	for iteration := 0; iteration < 2; iteration++ {
		inputs := bundle.PreparationInputs(accepted.Binding)
		child, err := io.ReadAll(inputs.Child)
		if err != nil || !bytes.Equal(child, expected[0]) {
			t.Fatal("retained child changed")
		}
		adapter, err := io.ReadAll(inputs.Adapter)
		if err != nil || !bytes.Equal(adapter, expected[1]) || !bytes.Equal(inputs.InputSchema, expected[4]) || !bytes.Equal(inputs.OutputSchema, expected[5]) {
			t.Fatal("retained bundle changed")
		}
		if inputs.Artifact["executable_sha256"] != bytesDigest(expected[0]) || inputs.Artifact["adapter_sha256"] != bytesDigest(expected[1]) {
			t.Fatal("retained descriptor changed")
		}
	}
}

func TestBundleRejectsMissingOrReplacedObjects(t *testing.T) {
	names := []string{"child", "adapter", "build-evidence", "module-graph", "input-schema", "output-schema"}
	for index, name := range names {
		t.Run(name, func(t *testing.T) {
			artifact, input, output, _, objects := bundleFixture(t)
			objects[index] = []byte("replaced object")
			if bundle, err := VerifyBundle(artifact, input, output, bundleReaders(objects)); err == nil || bundle != nil {
				t.Fatal("replaced object admitted")
			}
		})
	}
	artifact, input, output, readers, _ := bundleFixture(t)
	readers.BuildEvidence = nil
	if bundle, err := VerifyBundle(artifact, input, output, readers); err == nil || bundle != nil {
		t.Fatal("absent evidence admitted")
	}
}

func TestBundleRejectsMalformedEvidenceAndUnsupportedSchemaEvenWithMatchingHashes(t *testing.T) {
	for _, index := range []int{2, 4, 5} {
		artifact, input, output, _, objects := bundleFixture(t)
		if index == 2 {
			objects[index] = []byte(`{"producer":"one","producer":"two"}`)
			artifact["build_evidence_sha256"] = bytesDigest(objects[index])
		} else {
			objects[index] = []byte(`{"type":"object","$ref":"https://untrusted.invalid/schema"}`)
			if index == 4 {
				input = bytesDigest(objects[index])
			} else {
				output = bytesDigest(objects[index])
			}
		}
		if bundle, err := VerifyBundle(artifact, input, output, bundleReaders(objects)); err == nil || bundle != nil {
			t.Fatal("invalid content admitted by matching digest")
		}
	}
}
