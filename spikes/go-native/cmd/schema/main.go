// A build-time static metadata tool. It does not link tenant workflow packages.
package main

import (
	"encoding/json"
	"fmt"
	"github.com/midtown-technology-group/bifrost-go/internal/schema"
	"os"
	"path/filepath"
	"strings"
)

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "usage: schema source-file Input|Output")
		os.Exit(2)
	}
	files, err := filepath.Glob(filepath.Join(filepath.Dir(os.Args[1]), "*.go"))
	if err != nil {
		os.Exit(1)
	}
	count := 0
	for _, f := range files {
		if !strings.HasSuffix(f, "_test.go") {
			count++
		}
	}
	if count != 1 {
		fmt.Fprintln(os.Stderr, "static profile requires one application type file; cross-file methods need a stronger extractor")
		os.Exit(1)
	}
	b, err := os.ReadFile(os.Args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, "source unavailable")
		os.Exit(1)
	}
	s, err := schema.Extract(string(b), os.Args[2])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if json.NewEncoder(os.Stdout).Encode(s) != nil {
		os.Exit(1)
	}
}
