package readiness

import (
	"context"
	"errors"
	"fmt"
	bifrost "github.com/midtown-technology-group/bifrost-go"
	"sort"
	"strings"
)

type Input struct {
	IntegrationName string   `json:"integration_name"`
	RequiredKeys    []string `json:"required_keys,omitempty"`
}
type Output struct {
	Ready       bool     `json:"ready"`
	MissingKeys []string `json:"missing_keys"`
}
type IntegrationReader interface {
	Get(context.Context, string, *bifrost.GetIntegrationOptions) (*bifrost.Integration, error)
}

// Run is ordinary Go and can be unit tested without a runtime or live instance.
func Run(ctx context.Context, in Input, reader IntegrationReader) (Output, error) {
	name := strings.TrimSpace(in.IntegrationName)
	if name == "" {
		return Output{}, errors.New("integration_name is required")
	}
	cfg, err := reader.Get(ctx, name, nil)
	if err != nil {
		return Output{}, fmt.Errorf("integration lookup: %w", err)
	}
	missing := make([]string, 0)
	seen := map[string]bool{}
	for _, key := range in.RequiredKeys {
		key = strings.TrimSpace(key)
		if key == "" || seen[key] {
			continue
		}
		seen[key] = true
		if !cfg.HasConfigValue(key) {
			missing = append(missing, key)
		}
	}
	sort.Strings(missing)
	return Output{Ready: cfg != nil && len(missing) == 0, MissingKeys: missing}, nil
}
