package executionprofile

import (
	"encoding/json"
	"math"
	"strconv"
)

// Lexical checks reuse this spike's own Go P0 parser without changing P0.
func validEscapes(raw []byte) bool {
	quoted := false
	for i := 0; i < len(raw); i++ {
		if raw[i] == '"' {
			quoted = !quoted
			continue
		}
		if !quoted || raw[i] != '\\' {
			continue
		}
		i++
		if i >= len(raw) {
			return false
		}
		if raw[i] != 'u' {
			continue
		}
		if i+4 >= len(raw) {
			return false
		}
		n, e := strconv.ParseUint(string(raw[i+1:i+5]), 16, 16)
		if e != nil {
			return false
		}
		i += 4
		if n >= 0xdc00 && n <= 0xdfff {
			return false
		}
		if n >= 0xd800 && n <= 0xdbff {
			if i+6 >= len(raw) || raw[i+1] != '\\' || raw[i+2] != 'u' {
				return false
			}
			low, e := strconv.ParseUint(string(raw[i+3:i+7]), 16, 16)
			if e != nil || low < 0xdc00 || low > 0xdfff {
				return false
			}
			i += 6
		}
	}
	return true
}
func tree(d *json.Decoder, depth int) (any, error) {
	token, e := d.Token()
	if e != nil {
		return nil, InvalidJSON
	}
	if delimiter, ok := token.(json.Delim); ok {
		if depth >= MaxDepth {
			return nil, InvalidJSON
		}
		switch delimiter {
		case '{':
			object := map[string]any{}
			for d.More() {
				key, e := d.Token()
				if e != nil {
					return nil, InvalidJSON
				}
				name, ok := key.(string)
				if !ok {
					return nil, InvalidJSON
				}
				if _, present := object[name]; present {
					return nil, InvalidJSON
				}
				value, e := tree(d, depth+1)
				if e != nil {
					return nil, e
				}
				object[name] = value
			}
			end, e := d.Token()
			if e != nil || end != json.Delim('}') {
				return nil, InvalidJSON
			}
			return object, nil
		case '[':
			array := []any{}
			for d.More() {
				value, e := tree(d, depth+1)
				if e != nil {
					return nil, e
				}
				array = append(array, value)
			}
			end, e := d.Token()
			if e != nil || end != json.Delim(']') {
				return nil, InvalidJSON
			}
			return array, nil
		default:
			return nil, InvalidJSON
		}
	}
	if n, ok := token.(json.Number); ok {
		f, e := strconv.ParseFloat(string(n), 64)
		if e != nil || math.IsInf(f, 0) || math.IsNaN(f) {
			return nil, InvalidJSON
		}
	}
	return token, nil
}
