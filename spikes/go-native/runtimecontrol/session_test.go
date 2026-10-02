package runtimecontrol

import (
	"encoding/json"
	"errors"
	"os"
	"testing"
)

func TestPublishedSessionVectors(t *testing.T) {
	raw, e := os.ReadFile("testdata/control-vectors.json")
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct {
		Sessions []struct {
			Name            string  `json:"name"`
			Binding         Binding `json:"binding"`
			ExpectedBinding string  `json:"expected_binding"`
			Events          []struct {
				Action        string          `json:"action"`
				Direction     Direction       `json:"direction"`
				Process       string          `json:"process_identity"`
				Frame         json.RawMessage `json:"frame"`
				Authorization Authorization   `json:"authorization"`
				Expected      string          `json:"expected"`
			} `json:"events"`
		} `json:"sessions"`
	}
	if e = json.Unmarshal(raw, &corpus); e != nil {
		t.Fatal(e)
	}
	if len(corpus.Sessions) != 35 {
		t.Fatal("session corpus changed; reconcile contract")
	}
	for _, v := range corpus.Sessions {
		t.Run(v.Name, func(t *testing.T) {
			s, e := NewSession(v.Binding)
			if v.ExpectedBinding != "ok" {
				if !errors.Is(e, Code(v.ExpectedBinding)) {
					t.Fatalf("binding: %v", e)
				}
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			for i, event := range v.Events {
				before := s.State()
				switch event.Action {
				case "authorize":
					e = s.Authorize(event.Authorization)
				case "frame":
					var f Frame
					f, e = Decode(event.Frame)
					if e == nil {
						e = s.Accept(event.Direction, event.Process, f)
					}
				default:
					t.Fatalf("unknown fixture action %s", event.Action)
				}
				if event.Expected == "ok" || choice(event.Expected, string(AwaitHello), string(Prepared), string(Executing), string(Cancelling), string(StoppedObserved)) {
					if e != nil {
						t.Fatalf("event %d: %v", i, e)
					}
					if event.Expected != "ok" && s.State() != State(event.Expected) {
						t.Fatalf("event %d state %s want %s", i, s.State(), event.Expected)
					}
				} else {
					if !errors.Is(e, Code(event.Expected)) {
						t.Fatalf("event %d: %v want %s", i, e, event.Expected)
					}
					if s.State() != before {
						t.Fatalf("rejected event %d changed state", i)
					}
				}
			}
		})
	}
}
