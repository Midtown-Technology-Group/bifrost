// executioncheck exchanges synthetic proposed-profile frames, never workloads.
package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"

	"github.com/midtown-technology-group/bifrost-go/executionprofile"
)

const profile = "bifrost.runtime/v1/execution_profile/v1"

type encoded struct {
	Name string `json:"name"`
	Hex  string `json:"frame_hex"`
}
type exchange struct {
	Profile string    `json:"profile"`
	Frames  []encoded `json:"frames"`
}

func bounded(path string) ([]byte, error) {
	info, err := os.Stat(path)
	if err != nil || info.Size() > 2*1024*1024 {
		return nil, fmt.Errorf("invalid synthetic exchange")
	}
	return os.ReadFile(path)
}

func readFrame(raw []byte) (executionprofile.Frame, error) {
	reader := bytes.NewReader(raw)
	decoded, err := executionprofile.Read(reader)
	if err != nil || reader.Len() != 0 {
		return nil, fmt.Errorf("invalid synthetic frame")
	}
	return decoded.Frame, nil
}

func run() error {
	if len(os.Args) < 3 {
		return fmt.Errorf("invalid exchange operation")
	}
	raw, err := bounded(os.Args[1])
	if err != nil {
		return err
	}
	var vectors []struct {
		Name  string  `json:"name"`
		Hex   string  `json:"hex"`
		Error *string `json:"error"`
	}
	if json.Unmarshal(raw, &vectors) != nil || len(vectors) != 94 {
		return fmt.Errorf("unreviewed synthetic corpus")
	}
	golden := map[string]executionprofile.Frame{}
	for _, vector := range vectors {
		if vector.Error != nil || vector.Hex == "" {
			continue
		}
		if _, duplicate := golden[vector.Name]; duplicate {
			return fmt.Errorf("duplicate synthetic name")
		}
		raw, err := hex.DecodeString(vector.Hex)
		if err != nil {
			return fmt.Errorf("invalid synthetic hex")
		}
		frame, err := readFrame(raw)
		if err != nil {
			return err
		}
		golden[vector.Name] = frame
	}
	if len(golden) != 18 {
		return fmt.Errorf("changed synthetic golden inventory")
	}
	switch {
	case len(os.Args) == 3 && os.Args[2] == "emit":
		names := make([]string, 0, len(golden))
		for name := range golden {
			names = append(names, name)
		}
		sort.Strings(names)
		output := exchange{Profile: profile, Frames: []encoded{}}
		for _, name := range names {
			var buffer bytes.Buffer
			if err := executionprofile.Write(&buffer, golden[name]); err != nil {
				return err
			}
			output.Frames = append(output.Frames, encoded{name, hex.EncodeToString(buffer.Bytes())})
		}
		return json.NewEncoder(os.Stdout).Encode(output)
	case len(os.Args) == 4 && os.Args[2] == "validate":
		raw, err := bounded(os.Args[3])
		if err != nil {
			return err
		}
		var input exchange
		decoder := json.NewDecoder(bytes.NewReader(raw))
		decoder.DisallowUnknownFields()
		if decoder.Decode(&input) != nil || input.Profile != profile || len(input.Frames) != len(golden) {
			return fmt.Errorf("invalid synthetic exchange")
		}
		if _, err = decoder.Token(); err != io.EOF {
			return fmt.Errorf("trailing synthetic exchange")
		}
		seen := map[string]bool{}
		for _, encoded := range input.Frames {
			expected, present := golden[encoded.Name]
			if !present || seen[encoded.Name] {
				return fmt.Errorf("unknown synthetic vector")
			}
			seen[encoded.Name] = true
			raw, err := hex.DecodeString(encoded.Hex)
			if err != nil {
				return fmt.Errorf("invalid synthetic hex")
			}
			frame, err := readFrame(raw)
			if err != nil {
				return err
			}
			actualJSON, _ := json.Marshal(frame)
			expectedJSON, _ := json.Marshal(expected)
			if !bytes.Equal(actualJSON, expectedJSON) {
				return fmt.Errorf("synthetic peer mismatch")
			}
		}
		fmt.Printf("validated %d synthetic proposed-profile peer encodings\n", len(golden))
		return nil
	default:
		return fmt.Errorf("invalid exchange operation")
	}
}

func main() {
	if run() != nil {
		fmt.Fprintln(os.Stderr, "proposed profile exchange failed")
		os.Exit(1)
	}
}
