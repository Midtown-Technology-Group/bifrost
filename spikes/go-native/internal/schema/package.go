package schema

import (
	"errors"
	"go/ast"
	"go/build"
	"go/parser"
	"go/token"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

const maxPackageBytes = 4 << 20
const maxPackageFiles = 128

// ExtractPackage reads the ordinary non-test files selected for the initial
// Linux/amd64, CGO-disabled build target. Build tags are evaluated statically by
// the Go toolchain. No application import, type initializer, build or hook runs.
// The supported field types remain the same deliberately small static profile.
func ExtractPackage(directory, typeName string) (map[string]any, error) {
	info, err := os.Lstat(directory)
	if err != nil || !info.IsDir() {
		return nil, errors.New("schema: package directory unavailable")
	}
	entries, err := os.ReadDir(directory)
	if err != nil {
		return nil, errors.New("schema: package directory unavailable")
	}
	context := build.Default
	context.GOOS, context.GOARCH, context.CgoEnabled = "linux", "amd64", false
	combined := &ast.File{}
	packageName := ""
	total, count := 0, 0
	for _, entry := range entries {
		name := entry.Name()
		if !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") || strings.HasPrefix(name, ".") || strings.HasPrefix(name, "_") {
			continue
		}
		count++
		info, err := entry.Info()
		if err != nil || !info.Mode().IsRegular() || info.Size() > maxPackageBytes-int64(total) || count > maxPackageFiles {
			return nil, errors.New("schema: package input exceeds static profile")
		}
		matched, err := context.MatchFile(directory, name)
		if err != nil {
			return nil, errors.New("schema: invalid build constraint")
		}
		if !matched {
			continue
		}
		file, err := os.Open(filepath.Join(directory, name))
		if err != nil {
			return nil, errors.New("schema: package source unavailable")
		}
		raw, readErr := io.ReadAll(io.LimitReader(file, int64(maxPackageBytes-total)+1))
		closeErr := file.Close()
		total += len(raw)
		if readErr != nil || closeErr != nil || total > maxPackageBytes {
			return nil, errors.New("schema: package source unavailable or oversized")
		}
		parsed, err := parser.ParseFile(token.NewFileSet(), name, raw, 0)
		if err != nil {
			return nil, errors.New("schema: invalid Go source")
		}
		cgo := false
		for _, imported := range parsed.Imports {
			path, err := strconv.Unquote(imported.Path.Value)
			if err != nil {
				return nil, errors.New("schema: invalid import")
			}
			cgo = cgo || path == "C"
		}
		if cgo {
			continue
		}
		if packageName != "" && packageName != parsed.Name.Name {
			return nil, errors.New("schema: conflicting package declarations")
		}
		packageName = parsed.Name.Name
		combined.Decls = append(combined.Decls, parsed.Decls...)
	}
	return extractFile(combined, typeName)
}
