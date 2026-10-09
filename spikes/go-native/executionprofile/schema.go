package executionprofile

import (
	"embed"
	"encoding/json"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

// These are exact proposed shared documents, not Rust serialization types.
// No user-supplied schemas, remote references or executable hooks are loaded.
//
//go:embed schemas/*.json
var documents embed.FS

var registry = loadSchemas()
var profileID = "https://contracts.bifrost.invalid/runtime/v1/execution-profile/profile.schema.json"

func loadSchemas() map[string]map[string]any {
	registry := map[string]map[string]any{}
	entries, err := documents.ReadDir("schemas")
	if err != nil {
		panic("missing trusted proposal schemas")
	}
	for _, entry := range entries {
		raw, err := documents.ReadFile("schemas/" + entry.Name())
		var schema map[string]any
		if err != nil || json.Unmarshal(raw, &schema) != nil {
			panic("invalid trusted proposal schema")
		}
		registry[schema["$id"].(string)] = schema
	}
	return registry
}

func integer(v any) (float64, bool) {
	n, ok := v.(json.Number)
	if !ok || n == "-0" || strings.ContainsAny(string(n), ".eE") {
		return 0, false
	}
	f, err := strconv.ParseFloat(string(n), 64)
	return f, err == nil
}

// valid implements only the vocabulary of these pinned documents. It fails
// closed on new keywords; it is not a general-purpose public schema engine.
func valid(schema map[string]any, value any, root map[string]any) bool {
	for key := range schema {
		switch key {
		case "$schema", "$id", "$defs", "$ref", "title", "description", "type", "const", "enum", "oneOf", "anyOf", "properties", "required", "additionalProperties", "items", "uniqueItems", "minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum", "pattern", "format":
		default:
			return false
		}
	}
	if ref, ok := schema["$ref"].(string); ok {
		parts := strings.SplitN(ref, "#", 2)
		target := root
		if parts[0] != "" {
			target = registry[parts[0]]
		}
		if target == nil {
			return false
		}
		newRoot := target
		if len(parts) == 2 {
			for _, part := range strings.Split(strings.TrimPrefix(parts[1], "/"), "/") {
				target, ok = target[part].(map[string]any)
				if !ok {
					return false
				}
			}
		}
		if !valid(target, value, newRoot) {
			return false
		}
	}
	for _, keyword := range []string{"oneOf", "anyOf"} {
		if variants, ok := schema[keyword].([]any); ok {
			matches := 0
			for _, variant := range variants {
				if valid(variant.(map[string]any), value, root) {
					matches++
				}
			}
			if matches == 0 || (keyword == "oneOf" && matches != 1) {
				return false
			}
		}
	}
	if expected, ok := schema["const"]; ok && !reflect.DeepEqual(value, expected) {
		return false
	}
	if options, ok := schema["enum"].([]any); ok {
		found := false
		for _, option := range options {
			found = found || reflect.DeepEqual(value, option)
		}
		if !found {
			return false
		}
	}
	if kind, ok := schema["type"].(string); ok {
		matches := false
		switch kind {
		case "null":
			matches = value == nil
		case "object":
			_, matches = value.(map[string]any)
		case "array":
			_, matches = value.([]any)
		case "string":
			_, matches = value.(string)
		case "integer":
			_, matches = integer(value)
		}
		if !matches {
			return false
		}
	}
	if object, ok := value.(map[string]any); ok {
		properties, _ := schema["properties"].(map[string]any)
		if required, ok := schema["required"].([]any); ok {
			for _, key := range required {
				if _, present := object[key.(string)]; !present {
					return false
				}
			}
		}
		for key, item := range object {
			if property, present := properties[key]; present {
				if !valid(property.(map[string]any), item, root) {
					return false
				}
			} else if schema["additionalProperties"] == false {
				return false
			}
		}
	}
	if array, ok := value.([]any); ok {
		if n, ok := schema["minItems"].(float64); ok && float64(len(array)) < n {
			return false
		}
		if n, ok := schema["maxItems"].(float64); ok && float64(len(array)) > n {
			return false
		}
		for i, item := range array {
			if items, ok := schema["items"].(map[string]any); ok && !valid(items, item, root) {
				return false
			}
			if schema["uniqueItems"] == true {
				for _, prior := range array[:i] {
					if reflect.DeepEqual(item, prior) {
						return false
					}
				}
			}
		}
	}
	if s, ok := value.(string); ok {
		n := float64(utf8.RuneCountInString(s))
		if limit, ok := schema["minLength"].(float64); ok && n < limit {
			return false
		}
		if limit, ok := schema["maxLength"].(float64); ok && n > limit {
			return false
		}
		if pattern, ok := schema["pattern"].(string); ok {
			expression, err := regexp.Compile(pattern)
			if err != nil || !expression.MatchString(s) {
				return false
			}
		}
		if format, ok := schema["format"].(string); ok {
			if format != "date-time" {
				return false
			}
			if _, err := time.Parse(time.RFC3339Nano, s); err != nil {
				return false
			}
		}
	}
	if n, ok := integer(value); ok {
		if min, ok := schema["minimum"].(float64); ok && n < min {
			return false
		}
		if max, ok := schema["maximum"].(float64); ok && n > max {
			return false
		}
	}
	return true
}
