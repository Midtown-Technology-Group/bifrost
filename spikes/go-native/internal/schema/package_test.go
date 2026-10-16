package schema

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func writePackage(t *testing.T, files map[string]string) string {
	t.Helper()
	directory := t.TempDir()
	for name, source := range files {
		if err := os.WriteFile(filepath.Join(directory, name), []byte(source), 0600); err != nil {
			t.Fatal(err)
		}
	}
	return directory
}

func TestPackageExtractionIncludesAllFilesWithoutRunningCode(t *testing.T) {
	directory := writePackage(t, map[string]string{
		"input.go":    "package workflow; type Input struct { Name string `json:\"name\"` }",
		"output.go":   "package workflow; type Output struct { Ready bool `json:\"ready\"` }",
		"workflow.go": "package workflow; var initialized = panicNow(); func init() { panic(\"must not run\") }",
	})
	for name, source := range map[string]string{
		"Input":  "package p; type Input struct { Name string `json:\"name\"` }",
		"Output": "package p; type Output struct { Ready bool `json:\"ready\"` }",
	} {
		want, err := Extract(source, name)
		if err != nil {
			t.Fatal(err)
		}
		got, err := ExtractPackage(directory, name)
		if err != nil || !reflect.DeepEqual(got, want) {
			t.Fatalf("%s: got %v, %v", name, got, err)
		}
	}
}

func TestPackageExtractionFindsSerializationHooksInOtherFiles(t *testing.T) {
	for _, method := range []string{"MarshalJSON", "UnmarshalJSON", "MarshalText", "UnmarshalText"} {
		directory := writePackage(t, map[string]string{
			"input.go": "package p; type Input struct { Name string }",
			"codec.go": "package p; func (*Input) " + method + "() {}",
		})
		if _, err := ExtractPackage(directory, "Input"); err == nil {
			t.Fatalf("cross-file %s accepted", method)
		}
	}
}

func TestPackageExtractionUsesLinuxAMD64BuildFiles(t *testing.T) {
	directory := writePackage(t, map[string]string{
		"input_linux_amd64.go": "package p; type Input struct { Name string }",
		"input_windows.go":     "package p; type Input map[string]string; func (Input) MarshalJSON() {}",
		"input_arm64.go":       "package p; type Input map[string]string",
		"custom.go":            "//go:build customtag\n\npackage p; type Input map[string]string",
		"input_test.go":        "invalid test source is not application metadata",
		"_ignored.go":          "invalid ignored source",
		".ignored.go":          "invalid hidden source",
	})
	if _, err := ExtractPackage(directory, "Input"); err != nil {
		t.Fatal(err)
	}
}

func TestPackageExtractionExcludesCGOFiles(t *testing.T) {
	directory := writePackage(t, map[string]string{
		"input.go":  "package p; type Input struct { Name string }",
		"native.go": "package p; import \"C\"; func (Input) MarshalJSON() {}",
	})
	if _, err := ExtractPackage(directory, "Input"); err != nil {
		t.Fatal(err)
	}
}

func TestPackageExtractionRejectsAmbiguousOrUnsupportedSources(t *testing.T) {
	for name, source := range map[string]string{
		"duplicate": "package p; type Input struct { Other bool }",
		"package":   "package other; type Output struct { Other bool }",
		"shadow":    "package p; type string bool",
		"syntax":    "package p; type Broken",
	} {
		directory := writePackage(t, map[string]string{"input.go": "package p; type Input struct { Name string }", "other.go": source})
		if _, err := ExtractPackage(directory, "Input"); err == nil {
			t.Fatalf("%s accepted", name)
		}
	}
}

func TestPackageExtractionRejectsSymlinkAndBoundedInputs(t *testing.T) {
	directory := writePackage(t, map[string]string{"input.go": "package p; type Input struct { Name string }"})
	if err := os.Symlink(filepath.Join(directory, "input.go"), filepath.Join(directory, "link.go")); err != nil {
		t.Fatal(err)
	}
	if _, err := ExtractPackage(directory, "Input"); err == nil {
		t.Fatal("symlink accepted")
	}
	if err := os.Remove(filepath.Join(directory, "link.go")); err != nil {
		t.Fatal(err)
	}
	file, err := os.Create(filepath.Join(directory, "oversized.go"))
	if err != nil {
		t.Fatal(err)
	}
	if err := file.Truncate(maxPackageBytes + 1); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := ExtractPackage(directory, "Input"); err == nil {
		t.Fatal("oversized source accepted")
	}
}
