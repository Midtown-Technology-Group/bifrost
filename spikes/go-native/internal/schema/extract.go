// Package schema statically extracts a deliberately small JSON type profile.
// It parses source; it never imports, compiles or executes application code.
package schema

import (
	"errors"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"reflect"
	"sort"
	"strconv"
	"strings"
)

func Extract(source, typeName string) (map[string]any, error) {
	f, err := parser.ParseFile(token.NewFileSet(), "workflow.go", source, 0)
	if err != nil {
		return nil, errors.New("schema: invalid Go source")
	}
	for _, decl := range f.Decls {
		if method, ok := decl.(*ast.FuncDecl); ok && method.Recv != nil {
			switch method.Name.Name {
			case "MarshalJSON", "UnmarshalJSON", "MarshalText", "UnmarshalText":
				return nil, errors.New("schema: custom serialization requires an explicit reviewed schema")
			}
		}
	}
	for _, decl := range f.Decls {
		g, ok := decl.(*ast.GenDecl)
		if !ok {
			continue
		}
		for _, spec := range g.Specs {
			t, ok := spec.(*ast.TypeSpec)
			if !ok || t.Name.Name != typeName {
				continue
			}
			if t.TypeParams != nil {
				return nil, errors.New("schema: generic declarations are unsupported")
			}
			st, ok := t.Type.(*ast.StructType)
			if !ok {
				return nil, errors.New("schema: expected a struct")
			}
			properties := map[string]any{}
			required := make([]string, 0)
			for _, field := range st.Fields.List {
				if len(field.Names) != 1 {
					return nil, errors.New("schema: embedded or grouped fields are unsupported")
				}
				name := field.Names[0].Name
				if !ast.IsExported(name) {
					continue
				}
				optional := false
				if field.Tag != nil {
					raw, e := strconv.Unquote(field.Tag.Value)
					if e != nil {
						return nil, errors.New("schema: invalid field tag")
					}
					parts := strings.Split(reflect.StructTag(raw).Get("json"), ",")
					if parts[0] == "-" {
						continue
					}
					if parts[0] != "" {
						name = parts[0]
					}
					for _, option := range parts[1:] {
						if option == "omitempty" {
							optional = true
						} else {
							return nil, errors.New("schema: unsupported JSON tag option")
						}
					}
				}
				if _, exists := properties[name]; exists {
					return nil, errors.New("schema: conflicting JSON fields")
				}
				value, e := fieldSchema(field.Type)
				if e != nil {
					return nil, fmt.Errorf("schema field %s: %w", name, e)
				}
				properties[name] = value
				if !optional {
					required = append(required, name)
				}
			}
			sort.Strings(required)
			return map[string]any{"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "additionalProperties": false, "required": required, "properties": properties}, nil
		}
	}
	return nil, errors.New("schema: type not found")
}
func fieldSchema(expr ast.Expr) (map[string]any, error) {
	if id, ok := expr.(*ast.Ident); ok {
		switch id.Name {
		case "string":
			return map[string]any{"type": "string"}, nil
		case "bool":
			return map[string]any{"type": "boolean"}, nil
		}
	}
	if array, ok := expr.(*ast.ArrayType); ok && array.Len == nil {
		item, err := fieldSchema(array.Elt)
		if err != nil {
			return nil, err
		}
		return map[string]any{"type": []string{"array", "null"}, "items": item}, nil
	}
	return nil, errors.New("type outside safe static profile (use explicit schema and codec proof)")
}
