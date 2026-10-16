// A build-time static metadata tool. It does not link tenant workflow packages.
package main

import (
	"encoding/json"
	"fmt"
	"github.com/midtown-technology-group/bifrost-go/internal/schema"
	"os"
	"path/filepath"
)

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "usage: schema package-directory|source-file TypeName")
		os.Exit(2)
	}
	directory := os.Args[1]
	info, err := os.Lstat(directory)
	if err != nil {
		fmt.Fprintln(os.Stderr, "source unavailable")
		os.Exit(1)
	}
	if !info.IsDir() {
		if !info.Mode().IsRegular() || filepath.Ext(directory) != ".go" {
			fmt.Fprintln(os.Stderr, "expected a package directory or Go source file")
			os.Exit(1)
		}
		directory = filepath.Dir(directory)
	}
	s, err := schema.ExtractPackage(directory, os.Args[2])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if json.NewEncoder(os.Stdout).Encode(s) != nil {
		os.Exit(1)
	}
}
