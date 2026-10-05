# Device peer lighthouse admission

Date: 2026-10-05
Affects: operators who opt in to the router-hosted Vojeto launcher

Adds authenticated device peer session admission and revocation through shared
platform jobs. The feature is disabled unless a launcher URL and mounted mTLS
credential file paths are configured. Sessions use locally generated endpoint
keys, a numeric IPv4 service destination and a deadline capped at 30 minutes.
Existing device execute permission and organization scope are required; runner
execution rechecks current permissions. Provisioning success is lighthouse
startup, not proof of end-to-end network connectivity.

No automatic router installation or device bootstrap occurs on upgrade. Human
review, router TLS enrollment, reviewed immutable artifact and ingress rollout
remain prerequisites. See [the architecture contract](../architecture/device-peer-sessions.md).
Rollback disables admissions and stops only the separate launcher service.
