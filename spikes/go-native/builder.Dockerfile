FROM golang:1.27.1-bookworm@sha256:966278043a40889499db9b0cd196fc789c37c385d41bd9a10cb1e7764af60cdc
# This is trusted tooling preparation; no tenant source or test executes here.
ENV GOTOOLCHAIN=local GOWORK=off CGO_ENABLED=0
RUN GOBIN=/opt/tools go install golang.org/x/vuln/cmd/govulncheck@v1.8.0
