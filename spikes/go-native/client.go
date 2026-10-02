// Package bifrost is an experimental capability client, not a lifecycle owner.
package bifrost

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const SDKVersion = "0.0.0-spike.1"
const maxResponse = 1 << 20

// Provision must come from the trusted supervisor, never workflow input.
// A server-enforced restricted grant is required in production; this spike
// cannot issue one or turn an application token into one.
type Provision struct {
	Endpoint       string `json:"endpoint"`
	Bearer         string `json:"bearer"`
	OrganizationID string `json:"organization_id"`
	SolutionID     string `json:"solution_id,omitempty"`
}

type Client struct{ Integrations *IntegrationService }
type IntegrationService struct {
	endpoint  string
	provision Provision
	http      *http.Client
}

// NewClient requires HTTPS and never follows redirects. roots may supply a
// private test CA; certificate and hostname verification remain enabled.
func NewClient(p Provision, roots *x509.CertPool) (*Client, error) {
	u, err := url.Parse(p.Endpoint)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return nil, errors.New("SDK endpoint must be an HTTPS origin")
	}
	if p.Bearer == "" || strings.ContainsAny(p.Bearer, "\r\n") {
		return nil, errors.New("runtime credential missing or invalid")
	}
	transport := &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: roots}}
	client := &http.Client{Transport: transport, Timeout: 10 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	return &Client{Integrations: &IntegrationService{endpoint: strings.TrimSuffix(p.Endpoint, "/"), provision: p, http: client}}, nil
}

// Close releases idle connections. It has no execution/lifecycle effect.
func (c *Client) Close() { c.Integrations.http.CloseIdleConnections() }

// Integration retains arbitrary configuration without converting JSON numbers
// to float64. OAuth is deliberately outside this first capability subset.
type Integration struct {
	IntegrationID string                     `json:"integration_id"`
	EntityID      *string                    `json:"entity_id"`
	EntityName    *string                    `json:"entity_name"`
	Config        map[string]json.RawMessage `json:"config"`
}

// HasConfigValue considers null, blank strings and empty arrays/objects absent;
// false and numeric zero are present, matching the selected readiness policy.
func (i *Integration) HasConfigValue(key string) bool {
	if i == nil {
		return false
	}
	raw, ok := i.Config[key]
	if !ok {
		return false
	}
	var value any
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	if d.Decode(&value) != nil {
		return false
	}
	switch v := value.(type) {
	case nil:
		return false
	case string:
		return strings.TrimSpace(v) != ""
	case []any:
		return len(v) > 0
	case map[string]any:
		return len(v) > 0
	default:
		return true
	}
}

type GetIntegrationOptions struct{}

// APIError exposes only a status, never a credential-bearing response body.
type APIError struct{ StatusCode int }

func (e *APIError) Error() string {
	return fmt.Sprintf("BiFrost SDK request failed (HTTP %d)", e.StatusCode)
}

// Get uses the supervisor-provided scope/selector. Nil options use that scope;
// arbitrary organization, OAuth override and entity selectors are not exposed.
// No automatic retry or credential renewal is implemented by this first subset.
func (s *IntegrationService) Get(ctx context.Context, name string, opts *GetIntegrationOptions) (*Integration, error) {
	if strings.TrimSpace(name) == "" {
		return nil, errors.New("integration name required")
	}
	body := struct {
		Name     string  `json:"name"`
		Scope    *string `json:"scope"`
		Solution string  `json:"solution,omitempty"`
	}{Name: name, Solution: s.provision.SolutionID}
	if s.provision.OrganizationID != "" {
		body.Scope = &s.provision.OrganizationID
	}
	b, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, s.endpoint+"/api/sdk/integrations/get", bytes.NewReader(b))
	if err != nil {
		return nil, errors.New("invalid SDK request")
	}
	req.Header.Set("Authorization", "Bearer "+s.provision.Bearer)
	req.Header.Set("Content-Type", "application/json")
	res, err := s.http.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, errors.New("SDK transport failed")
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK {
		return nil, &APIError{StatusCode: res.StatusCode}
	}
	b, err = io.ReadAll(io.LimitReader(res.Body, maxResponse+1))
	if err != nil || len(b) > maxResponse {
		return nil, errors.New("SDK response unavailable or too large")
	}
	var result *Integration
	if json.Unmarshal(b, &result) != nil {
		return nil, errors.New("invalid SDK integration response")
	}
	return result, nil
}
