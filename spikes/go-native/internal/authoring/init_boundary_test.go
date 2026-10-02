package authoring

import (
	"context"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

// Falsification test: a main-based authoring helper cannot gate ordinary Go
// initialization. Compilation happens in the isolated build/test plane only.
func TestMainGateCannotPreventPackageEffects(t *testing.T) {
	directory := t.TempDir()
	source := filepath.Join(directory, "main.go")
	program := `package main
import("io";"os")
func mark(s string) int {
 f,e:=os.OpenFile(os.Getenv("CANARY_PATH"),os.O_CREATE|os.O_APPEND|os.O_WRONLY,0600)
 if e!=nil { panic("synthetic canary unavailable") }; defer f.Close()
 _,_=io.WriteString(f,s+"\n");return 1
}
var variableInitializer=mark("variable-initializer")
func init(){mark("init-function")}
func main(){_,_=io.WriteString(os.Stdout,"ready\n");var b [1]byte;_,_=os.Stdin.Read(b[:])}
`
	if err := os.WriteFile(source, []byte(program), 0600); err != nil {
		t.Fatal(err)
	}
	binary := filepath.Join(directory, "canary")
	buildCtx, cancelBuild := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancelBuild()
	if output, err := exec.CommandContext(buildCtx, "go", "build", "-mod=readonly", "-trimpath", "-buildvcs=false", "-o", binary, source).CombinedOutput(); err != nil {
		t.Fatalf("isolated canary build: %v %s", err, output)
	}
	runCtx, cancelRun := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancelRun()
	cmd := exec.CommandContext(runCtx, binary)
	canary := filepath.Join(directory, "effects")
	cmd.Env = []string{"CANARY_PATH=" + canary, "PATH=/nonexistent"}
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	defer stdin.Close()
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err = cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = cmd.Process.Kill(); _ = cmd.Wait() }()
	ready := make([]byte, 6)
	if _, err = io.ReadFull(stdout, ready); err != nil || string(ready) != "ready\n" {
		t.Fatal("canary did not reach blocked main")
	}
	// No input/Start byte has been sent. Both effects are already committed.
	actual, err := os.ReadFile(canary)
	if err != nil || string(actual) != "variable-initializer\ninit-function\n" {
		t.Fatalf("initialization observation: %q %v", actual, err)
	}
}
