// controlcheck exchanges owned synthetic control encodings, never workloads.
package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"sort"

	"github.com/midtown-technology-group/bifrost-go/runtimecontrol"
)

const profile = "bifrost.runtime/v1/control_profile/v1"

type encoded struct {
	Name string `json:"name"`
	Hex  string `json:"frame_hex"`
}
type exchange struct {
	Profile string    `json:"profile"`
	Frames  []encoded `json:"frames"`
}

func bounded(path string) ([]byte, error) {
	info, e := os.Stat(path)
	if e != nil || info.Size() > 1024*1024 {
		return nil, fmt.Errorf("invalid synthetic exchange")
	}
	return os.ReadFile(path)
}
func run() error {
	if len(os.Args) < 3 {
		return fmt.Errorf("usage: controlcheck VECTORS emit | validate FILE")
	}
	raw, e := bounded(os.Args[1])
	if e != nil {
		return e
	}
	var corpus struct {
		Profile string `json:"profile"`
		Wire    []struct {
			Name     string `json:"name"`
			Expected string `json:"expected"`
			JSON     string `json:"json"`
		} `json:"wire"`
	}
	if e = json.Unmarshal(raw, &corpus); e != nil || corpus.Profile != profile {
		return fmt.Errorf("invalid synthetic corpus")
	}
	golden := map[string]runtimecontrol.Frame{}
	for _, v := range corpus.Wire {
		if v.Expected == "ok" {
			f, e := runtimecontrol.Decode([]byte(v.JSON))
			if e != nil {
				return e
			}
			golden[v.Name] = f
		}
	}
	if len(golden) == 0 {
		return fmt.Errorf("missing synthetic golden vectors")
	}
	switch {
	case len(os.Args) == 3 && os.Args[2] == "emit":
		names := make([]string, 0, len(golden))
		for name := range golden {
			names = append(names, name)
		}
		sort.Strings(names)
		out := exchange{Profile: profile, Frames: []encoded{}}
		for _, name := range names {
			var b bytes.Buffer
			if e = runtimecontrol.Write(&b, golden[name]); e != nil {
				return e
			}
			out.Frames = append(out.Frames, encoded{name, hex.EncodeToString(b.Bytes())})
		}
		return json.NewEncoder(os.Stdout).Encode(out)
	case len(os.Args) == 4 && os.Args[2] == "validate":
		raw, e = bounded(os.Args[3])
		if e != nil {
			return e
		}
		var x exchange
		decoder := json.NewDecoder(bytes.NewReader(raw))
		decoder.DisallowUnknownFields()
		if e = decoder.Decode(&x); e != nil || x.Profile != profile || len(x.Frames) != len(golden) {
			return fmt.Errorf("invalid synthetic exchange")
		}
		seen := map[string]bool{}
		for _, v := range x.Frames {
			expected, ok := golden[v.Name]
			if !ok || seen[v.Name] {
				return fmt.Errorf("unknown synthetic vector")
			}
			seen[v.Name] = true
			raw, e := hex.DecodeString(v.Hex)
			if e != nil {
				return fmt.Errorf("invalid synthetic hex")
			}
			r := bytes.NewReader(raw)
			f, e := runtimecontrol.Read(r)
			if e != nil || f == nil || r.Len() != 0 {
				return fmt.Errorf("invalid synthetic frame")
			}
			a, _ := json.Marshal(f)
			b, _ := json.Marshal(expected)
			if !bytes.Equal(a, b) {
				return fmt.Errorf("synthetic peer mismatch")
			}
		}
		fmt.Printf("validated %d synthetic peer control encodings\n", len(golden))
		return nil
	default:
		return fmt.Errorf("invalid controlcheck operation")
	}
}
func main() {
	if e := run(); e != nil {
		fmt.Fprintln(os.Stderr, "control exchange failed")
		os.Exit(1)
	}
}
