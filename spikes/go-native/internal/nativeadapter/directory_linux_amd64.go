//go:build linux && amd64

package nativeadapter

import (
	"bytes"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
)

// LoadSessionBundle reads a guardian-staged, read-only directory using a pinned
// local delivery index. It is not workflow registration or an acceptance API.
// The guardian must authenticate the index pin and accepted artifact association;
// the index binds per-session identity and cannot replace owner admission.
func LoadSessionBundle(directory, indexDigest string) (*VerifiedBundle, map[string]any, error) {
	open := func(name string) (*os.File, error) {
		file, err := os.Open(filepath.Join(directory, name))
		if err != nil {
			return nil, BundleRejected
		}
		stat, err := file.Stat()
		if err != nil || !stat.Mode().IsRegular() {
			_ = file.Close()
			return nil, BundleRejected
		}
		return file, nil
	}
	indexFile, err := open("session-bundle.json")
	if err != nil {
		return nil, nil, err
	}
	raw, err := bundleObject(indexFile, 65536, indexDigest)
	closeErr := indexFile.Close()
	if err != nil || closeErr != nil || !strictJSONDepth(raw, 64) || !closedObject(raw, "version", "binding", "artifact", "input_schema_sha256", "output_schema_sha256") {
		return nil, nil, BundleRejected
	}
	var index struct {
		Version      string         `json:"version"`
		Binding      map[string]any `json:"binding"`
		Artifact     map[string]any `json:"artifact"`
		InputDigest  string         `json:"input_schema_sha256"`
		OutputDigest string         `json:"output_schema_sha256"`
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	decoder.DisallowUnknownFields()
	if decoder.Decode(&index) != nil || index.Version != "isolated-native-session-bundle/v1" || index.Binding == nil || index.Artifact == nil {
		return nil, nil, BundleRejected
	}
	var opened []*os.File
	defer func() {
		for _, file := range opened {
			_ = file.Close()
		}
	}()
	var objects []io.Reader
	for _, name := range []string{"workflow", "build-evidence.json", "module-graph.txt", "input-schema.json", "output-schema.json"} {
		file, err := open(name)
		if err != nil {
			return nil, nil, err
		}
		opened = append(opened, file)
		objects = append(objects, file)
	}
	// Hash the actual running adapter, never a supplied substitute binary.
	adapter, err := os.Open("/proc/self/exe")
	if err != nil {
		return nil, nil, BundleRejected
	}
	defer adapter.Close()
	bundle, err := VerifyBundle(index.Artifact, index.InputDigest, index.OutputDigest, BundleReaders{Child: objects[0], Adapter: adapter, BuildEvidence: objects[1], ModuleGraph: objects[2], InputSchema: objects[3], OutputSchema: objects[4]})
	if err != nil {
		return nil, nil, err
	}
	return bundle, index.Binding, nil
}
