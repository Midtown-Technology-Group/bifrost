# Bifrost v3.2.0

## Changes

- docs(release): document incident and prevention plan [skip ci] (#1367)
- feat(release): reuse identical-tree CI evidence for preparation (#1365)
- fix(runtime): exclude untouched reservations from deployment drain (#1370)

## Fixed vulnerabilities

Security-related PR titles below are an inventory, not a verified CVE assessment. The release reviewer should add confirmed identifiers and impact before merging.

No PR titles matched the security filter; this does not establish that no vulnerabilities were fixed.

## Upgrade notes

CLI/server contract: 13 -> 13. Install the CLI bundled with this release; mismatched contracts are rejected.
Before upgrading, back up the database and review the exact release's migration heads. Apply migrations using the protected infrastructure migration lane.
Automatic rollback is not certified. Preserve terminal workflow retirement evidence and admission safeguards during application rollback. Database downgrade past 20261003_workflow_retirement refuses once retirement evidence exists; use a reviewed recovery plan, not an automatic schema downgrade.
Before enabling terminal Live retirement, review actual production caller/byte evidence. The native read-only census does not prove absence of Python, operational file, App Source/dist, or external callers. Coordinate owners and retain the exact reviewed evidence digest before authorizing retirement.
Read back retirement inventory to verify retained row/evidence IDs. An uncertain outcome requires readback before any exact retry; do not replay with fresh targets. Keep the forward schema during application-image rollback.
Follow the [terminal-retirement runbook](https://github.com/Midtown-Technology-Group/bifrost/blob/5fcae00cec1f0d8158fee47b58657e74f60cc1ec/docs/architecture/rapid-workspace-promotion.md#obsolete-registrations-in-the-same-cutover).
Contract comparison requires: no additional bump floor.
This release uses the MTG fork's independent SemVer; upstream versions are not its baseline.

## Contributors

@MTG-Thomas

## Source

Reviewed interval: `v3.1.0..5fcae00cec1f0d8158fee47b58657e74f60cc1ec`.

Publishing packages does not deploy the MTG production runtime.
