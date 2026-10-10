package nativeadapter

import (
	"encoding/json"
	"math/big"
	"strconv"
	"strings"
	"unicode/utf8"
)

// This bounded neutral subset validates workload data, not runtime wire frames.
// Unsupported schema keywords fail even when their property is absent.
func workloadMatches(schema, value any) bool {
	return supportedWorkload(schema, 0) && checkWorkload(schema, value, 0)
}

func supportedWorkload(schema any, depth int) bool {
	object, ok := schema.(map[string]any)
	if !ok || depth > 64 {
		return false
	}
	for key, value := range object {
		switch key {
		case "$schema":
			if value != "https://json-schema.org/draft/2020-12/schema" {
				return false
			}
		case "type":
			if kind, ok := value.(string); ok {
				if !workloadType(kind) {
					return false
				}
			} else {
				kinds, ok := value.([]any)
				if !ok || len(kinds) == 0 {
					return false
				}
				seen := map[string]bool{}
				for _, item := range kinds {
					kind, ok := item.(string)
					if !ok || !workloadType(kind) || seen[kind] {
						return false
					}
					seen[kind] = true
				}
			}
		case "properties":
			properties, ok := value.(map[string]any)
			if !ok {
				return false
			}
			for _, child := range properties {
				if !supportedWorkload(child, depth+1) {
					return false
				}
			}
		case "required":
			fields, ok := value.([]any)
			if !ok {
				return false
			}
			seen := map[string]bool{}
			for _, field := range fields {
				name, ok := field.(string)
				if !ok || seen[name] {
					return false
				}
				seen[name] = true
			}
		case "additionalProperties":
			if _, ok := value.(bool); !ok {
				return false
			}
		case "items":
			if !supportedWorkload(value, depth+1) {
				return false
			}
		case "minLength":
			n, ok := schemaNonnegativeInteger(value)
			if !ok || n > 65536 {
				return false
			}
		default:
			return false
		}
	}
	return true
}

func workloadType(kind string) bool {
	switch kind {
	case "object", "array", "string", "boolean", "null", "integer", "number":
		return true
	default:
		return false
	}
}

// Bound exponent expansion before big.Rat allocates attacker-sized integers.
// This initial workload subset rejects decimal exponents outside +/-65536.
func workloadNumber(value any) (*big.Rat, bool) {
	n, ok := value.(json.Number)
	if !ok {
		return nil, false
	}
	if index := strings.IndexAny(string(n), "eE"); index >= 0 {
		exponent, err := strconv.ParseInt(string(n)[index+1:], 10, 32)
		if err != nil || exponent < -65536 || exponent > 65536 {
			return nil, false
		}
	}
	r, ok := new(big.Rat).SetString(string(n))
	return r, ok
}

func schemaNonnegativeInteger(value any) (int64, bool) {
	r, ok := workloadNumber(value)
	if !ok || !r.IsInt() || !r.Num().IsInt64() {
		return 0, false
	}
	result := r.Num().Int64()
	return result, result >= 0
}

func workloadKind(kind string, value any) bool {
	switch kind {
	case "null":
		return value == nil
	case "object":
		_, ok := value.(map[string]any)
		return ok
	case "array":
		_, ok := value.([]any)
		return ok
	case "string":
		_, ok := value.(string)
		return ok
	case "boolean":
		_, ok := value.(bool)
		return ok
	case "number", "integer":
		r, ok := workloadNumber(value)
		return ok && (kind == "number" || r.IsInt())
	default:
		return false
	}
}

func checkWorkload(schema, value any, depth int) bool {
	if depth > 64 {
		return false
	}
	object := schema.(map[string]any)
	if kinds, exists := object["type"]; exists {
		accepted := false
		if kind, ok := kinds.(string); ok {
			accepted = workloadKind(kind, value)
		} else {
			for _, kind := range kinds.([]any) {
				accepted = accepted || workloadKind(kind.(string), value)
			}
		}
		if !accepted {
			return false
		}
	}
	if text, ok := value.(string); ok {
		if minimum, exists := object["minLength"]; exists {
			n, _ := schemaNonnegativeInteger(minimum)
			if int64(utf8.RuneCountInString(text)) < n {
				return false
			}
		}
	}
	if fields, ok := value.(map[string]any); ok {
		if required, exists := object["required"]; exists {
			for _, name := range required.([]any) {
				if _, exists := fields[name.(string)]; !exists {
					return false
				}
			}
		}
		properties, _ := object["properties"].(map[string]any)
		for name, child := range fields {
			if property, exists := properties[name]; exists {
				if !checkWorkload(property, child, depth+1) {
					return false
				}
			} else if object["additionalProperties"] == false {
				return false
			}
		}
	}
	if items, ok := value.([]any); ok {
		if itemSchema, exists := object["items"]; exists {
			for _, item := range items {
				if !checkWorkload(itemSchema, item, depth+1) {
					return false
				}
			}
		}
	}
	return true
}
