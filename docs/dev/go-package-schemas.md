# Static schema discovery for a Go package

Input, output and handler code can live in separate ordinary Go source files.
The schema command reads the package without importing, compiling or running
the application. Package variables and `init` functions are never executed for
metadata discovery.

In the Go spike module, build the trusted tool using the ordinary build plane:

```sh
go build -mod=readonly -o /tmp/bifrost-schema ./cmd/schema
/tmp/bifrost-schema ./internal/readiness Input
/tmp/bifrost-schema ./internal/readiness/readiness.go Output
```

Both forms inspect the entire selected package, so a serialization method in a
different file cannot silently escape detection. A source-file argument selects
its package directory rather than limiting inspection to that one file.

Discovery uses the current Go toolchain's static build-file rules for the
initial Linux/amd64, CGO-disabled target. Test files, hidden/underscore-prefixed
files, other platform files, unsatisfied build tags and files importing `C`
are excluded. No `go list`, dependency installation or application code execution
is used. Custom build-tag overrides are not supported by this recipe.

The existing conservative type profile still applies: a named struct containing
strings, booleans and slices, with supported JSON tags. Pointers, maps,
interfaces, named field types, cross-package references, custom serialization,
embedded/grouped fields and generic root types require explicit reviewed schemas.
Splitting declarations into files does not broaden those inference rules.
Duplicate type declarations, primitive type shadowing, selected package-name
conflicts and serialization methods anywhere in the selected package reject.
The AST reader is not a replacement for `go test` or compiler type checking.

Package discovery rejects source symlinks, more than 128 candidate application
files or more than 4 MiB of selected source. It emits the same JSON Schema shape
as the single-source API, and preserves explicit business constraints such as
`minLength` in the separately reviewed manifest schemas. It does not register or
admit a workflow.

The `Go developer tools` hosted lane runs ordinary Go formatting, vet and tests
with no network and readonly source/dependencies, then builds this trusted tool
and extracts both types from a split-file fixture whose `init` panics. It also
runs the archive metadata checks. This lane validates developer tooling only;
it neither replaces runtime acceptance nor changes the frozen spike evidence.
