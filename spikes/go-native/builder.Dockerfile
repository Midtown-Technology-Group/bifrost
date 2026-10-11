FROM golang:1.27.2-bookworm@sha256:5cf287a799e6b94384bad13d16b14904c531f51ba65792237e122ce42b392f61
# This is trusted tooling preparation; no tenant source or test executes here.
ENV GOTOOLCHAIN=local GOWORK=off CGO_ENABLED=0
RUN GOBIN=/opt/tools go install golang.org/x/vuln/cmd/govulncheck@v1.8.0
