package schema

import (
	"encoding/json"
	"github.com/google/go-cmp/cmp"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

func TestStaticExtractionRejectsUnsafeTypes(t *testing.T) {
	for _, field := range []string{"any", "*string", "map[string]string", "time.Time", "Node", "[3]string"} {
		if _, err := Extract("package p; type Input struct { Value "+field+" }", "Input"); err == nil {
			t.Fatalf("unsafe inference accepted: %s", field)
		}
	}
	if _, err := Extract("package p; type Input[T any] struct { Value T }", "Input"); err == nil {
		t.Fatal("generic declaration accepted")
	}
	if _, err := Extract("package p; type Input struct { Value string }; func (Input) MarshalJSON() ([]byte,error) { return nil,nil }", "Input"); err == nil {
		t.Fatal("custom marshaler accepted")
	}
}

func TestParsingDoesNotExecuteInitialization(t *testing.T) {
	source := "package p; var hostile = panicNow(); func init() { panic(\"must not run\") }; type Input struct { Value string `json:\"value\"` }"
	got, err := Extract(source, "Input")
	if err != nil || got["type"] != "object" {
		t.Fatal("static metadata extraction failed")
	}
}

func TestDeclaredSchemasMatchTypes(t *testing.T) {
	files, err := filepath.Glob("../readiness/*.go")
	if err != nil {
		t.Fatal(err)
	}
	count := 0
	for _, f := range files {
		if !strings.HasSuffix(f, "_test.go") {
			count++
		}
	}
	if count != 1 {
		t.Fatal("single-file static schema profile exceeded")
	}
	bytes, err := os.ReadFile("../readiness/readiness.go")
	if err != nil {
		t.Fatal(err)
	}
	for _, typ := range []string{"Input", "Output"} {
		got, err := Extract(string(bytes), typ)
		if err != nil {
			t.Fatal(err)
		}
		// Nonempty/trimmed integration name is business validation, not inferred
		// from string. The explicit schema adds minLength conservatively.
		if typ == "Input" {
			got["properties"].(map[string]any)["integration_name"].(map[string]any)["minLength"] = 1
		}
		encoded, _ := json.Marshal(got)
		var actual, declared map[string]any
		if json.Unmarshal(encoded, &actual) != nil {
			t.Fatal("generated schema invalid")
		}
		file, err := os.ReadFile("../../schemas/" + strings.ToLower(typ) + ".json")
		if err != nil {
			t.Fatal(err)
		}
		if json.Unmarshal(file, &declared) != nil {
			t.Fatal("declared schema invalid")
		}
		for _, s := range []map[string]any{actual, declared} {
			sort.Slice(s["required"].([]any), func(i, j int) bool { return s["required"].([]any)[i].(string) < s["required"].([]any)[j].(string) })
		}
		if !cmp.Equal(actual, declared) {
			t.Fatalf("schema/type drift: %s", cmp.Diff(declared, actual))
		}
	}
}
