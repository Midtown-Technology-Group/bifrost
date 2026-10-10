package nativeadapter

import (
	"bytes"
	"encoding/json"
	"testing"
)

func schemaJSON(t *testing.T, raw string) any {
	t.Helper()
	d := json.NewDecoder(bytes.NewBufferString(raw))
	d.UseNumber()
	var value any
	if err := d.Decode(&value); err != nil {
		t.Fatal(err)
	}
	return value
}

func TestWorkloadSchemaExactTypesAndNullability(t *testing.T) {
	schema := schemaJSON(t, `{"type":"object","required":["name"],"additionalProperties":false,"properties":{"name":{"type":"string","minLength":1},"keys":{"type":["array","null"],"items":{"type":"string"}}}}`)
	for _, raw := range []string{`{"name":"fixture"}`, `{"name":"é","keys":null}`, `{"name":"fixture","keys":["key"]}`} {
		if !workloadMatches(schema, schemaJSON(t, raw)) {
			t.Fatal("valid input rejected")
		}
	}
	for _, raw := range []string{`{}`, `{"name":""}`, `{"name":false}`, `{"name":"fixture","keys":[false]}`, `{"name":"fixture","keys":"key"}`, `{"name":"fixture","extra":true}`} {
		if workloadMatches(schema, schemaJSON(t, raw)) {
			t.Fatal("invalid input accepted")
		}
	}
}

func TestWorkloadSchemaRejectsUnsupportedOptionalFields(t *testing.T) {
	for _, raw := range []string{`{"type":"object","properties":{"absent":{"type":"string","pattern":".*"}}}`, `{"type":"future"}`, `{"type":["string","string"]}`, `{"minLength":-1}`, `{"minLength":0.5}`} {
		if workloadMatches(schemaJSON(t, raw), schemaJSON(t, `{}`)) {
			t.Fatal("unsupported schema accepted")
		}
	}
}

func TestWorkloadIntegerHasNoFloatRounding(t *testing.T) {
	schema := schemaJSON(t, `{"type":"integer"}`)
	for _, raw := range []string{`1`, `1.0`, `1e2`, `9007199254740993`} {
		if !workloadMatches(schema, schemaJSON(t, raw)) {
			t.Fatal("exact integer rejected")
		}
	}
	for _, raw := range []string{`1.0000000000000000001`, `1e-2`, `true`, `"1"`} {
		if workloadMatches(schema, schemaJSON(t, raw)) {
			t.Fatal("fraction accepted as integer")
		}
	}
}
