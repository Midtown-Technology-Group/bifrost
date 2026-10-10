//go:build linux && amd64

package nativeadapter

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestSessionBundlePinsActualAdapterAndIndex(t *testing.T) {
	for _, variant := range []string{"valid", "index-drift", "adapter-substitute", "missing-graph"} {
		t.Run(variant, func(t *testing.T) {
			artifact, input, output, _, objects := bundleFixture(t)
			self, err := os.ReadFile("/proc/self/exe")
			if err != nil {
				t.Fatal(err)
			}
			artifact["adapter_sha256"] = bytesDigest(self)
			if variant == "adapter-substitute" {
				artifact["adapter_sha256"] = bytesDigest(objects[1])
			}
			_, accepted, _ := preparationFixture(t)
			index, err := json.Marshal(map[string]any{"version": "isolated-native-session-bundle/v1", "binding": accepted.Binding, "artifact": artifact, "input_schema_sha256": input, "output_schema_sha256": output})
			if err != nil {
				t.Fatal(err)
			}
			directory := t.TempDir()
			files := map[string][]byte{"session-bundle.json": index, "workflow": objects[0], "build-evidence.json": objects[2], "module-graph.txt": objects[3], "input-schema.json": objects[4], "output-schema.json": objects[5]}
			for name, raw := range files {
				if err := os.WriteFile(filepath.Join(directory, name), raw, 0600); err != nil {
					t.Fatal(err)
				}
			}
			pin := bytesDigest(index)
			if variant == "index-drift" {
				pin = bytesDigest([]byte("different accepted index"))
			}
			if variant == "missing-graph" {
				if err := os.Remove(filepath.Join(directory, "module-graph.txt")); err != nil {
					t.Fatal(err)
				}
			}
			bundle, binding, err := LoadSessionBundle(directory, pin)
			if variant == "valid" {
				if err != nil || bundle == nil || binding["session_id"] != accepted.Binding["session_id"] {
					t.Fatal("valid staged bytes rejected")
				}
			} else if err != BundleRejected || bundle != nil || binding != nil {
				t.Fatal("unbound staged bytes admitted")
			}
		})
	}
}
