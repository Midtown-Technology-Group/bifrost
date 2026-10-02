package readiness

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/google/go-cmp/cmp"
	bifrost "github.com/midtown-technology-group/bifrost-go"
	"testing"
)

type fakeReader struct {
	cfg  *bifrost.Integration
	err  error
	name string
}

func (f *fakeReader) Get(ctx context.Context, name string, _ *bifrost.GetIntegrationOptions) (*bifrost.Integration, error) {
	f.name = name
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	return f.cfg, f.err
}
func TestRun(t *testing.T) {
	for _, tc := range []struct {
		name    string
		cfg     *bifrost.Integration
		ready   bool
		missing []string
	}{
		{"configured", &bifrost.Integration{Config: map[string]json.RawMessage{"enabled": json.RawMessage(`false`)}}, true, []string{}},
		{"missing", &bifrost.Integration{Config: map[string]json.RawMessage{"enabled": json.RawMessage(`"  "`)}}, false, []string{"enabled"}},
		{"not_found", nil, false, []string{"enabled"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := &fakeReader{cfg: tc.cfg}
			out, err := Run(context.Background(), Input{IntegrationName: " Fixture ", RequiredKeys: []string{"enabled", " enabled ", ""}}, f)
			if err != nil || out.Ready != tc.ready || !cmp.Equal(out.MissingKeys, tc.missing) || f.name != "Fixture" {
				t.Fatalf("unexpected result: %+v, %v", out, err)
			}
		})
	}
}
func TestRunErrors(t *testing.T) {
	sentinel := errors.New("lookup unavailable")
	if _, err := Run(context.Background(), Input{IntegrationName: "Fixture"}, &fakeReader{err: sentinel}); !errors.Is(err, sentinel) {
		t.Fatal("error identity lost")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := Run(ctx, Input{IntegrationName: "Fixture"}, &fakeReader{}); !errors.Is(err, context.Canceled) {
		t.Fatal("cancellation lost")
	}
	f := &fakeReader{}
	if _, err := Run(context.Background(), Input{}, f); err == nil || f.name != "" {
		t.Fatal("invalid input caused capability call")
	}
}
