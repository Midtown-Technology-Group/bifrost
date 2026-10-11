# Bifrost v3.1.0

## Changes

- Isolate concurrent service claim test from the stack worker (#1076)
- Define the language-neutral runtime v1 document foundation (#1074)
- Update review expectation guidelines in delivery-lanes.md (#1075)
- Preserve captured Solution IDs and display metadata during adoption (#1064)
- Seal parent package initializers in immutable source closure (#1072)
- Fix strict Solution event admission without weakening argument validation (#1057)
- Settle delayed authored Source accounting from verified descendants (#1063)
- Consolidate guarded App publication and original-job readback recovery (#1071)
- Deliver protected Main Apps with original-job recovery and source accounting (#1077)
- fix(release): publish verified drafts by release ID (#1079)
- Integrate upstream through 686cff8d8 on current MTG main (#920)
- ci: stage fail-closed Sonar coverage and source-scope migration (#1068)
- Verify Solution App authored source and active compiled runtime before accounting (#1083)
- Stage Sonar Free-plan CI cutover for main and PR analysis (#1085)
- ci: stop cancelled-plan diagnostics and gate manual pre-PR on lint (#1086)
- Deliver complete Solution packages through the reviewed Main pipeline (#1088)
- Verify Sonar CI migration and retire automatic-analysis configuration (#1089)
- Merge upstream worker SDK transport and credential security (#1087)
- docs: adopt maintainer-directed contribution acceptance (#1080)
- fix: preserve owned-table workflow successor controls (#1090)
- Complete workflow package publication and preserve resource bindings (#1092)
- fix: allow guarded Solution runtime file updates (#1094)
- fix: reconcile mixed registry and legacy package delivery contracts (#1096)
- fix(ci): read nightly coverage from the exact worktree (#1099)
- fix: repair Sonar findings and collect complete runtime coverage (#1097)
- MIDT-205: correct Azure Blob and Redis production guidance (#1098)
- fix: complete Main Sonar security and coverage remediation (#1101)
- docs: refactor audit identifying review drag and unsafe boundaries (#1102)
- docs: record Code Mode compatibility preflight and proof gates (#1110)
- feat(devices): actionable device_busy and convergent, idempotent cancel (#1121)
- fix(devices): release claims of dead devices in the job sweep (#1123)
- fix(promotion): verify solution-managed live registrations in preview handoff (#1124)
- ci: stop allocating runners for zero-shard sentinel lanes and record occupancy (#1111)
- Upstream sync slice 1: port R1a operation catalog (metadata-only) (#1128)
- feat(solutions): verify signed workflow removal evidence (#1129)
- perf: avoid CRLF replacement scans for LF-only sync content (#1131)
- fix(security): update vulnerable runtime dependencies (#1134)
- Spike Pydantic DecisionModel dependency and TypeSafe contract (MIDT-274) (#968)
- Make client builds type-safe and document native compiler benchmarks (#1103)
- Prepare MCP image before freezing pre-PR provenance (#1115)
- Integrate upstream own-run visibility and MCP REST parity (#814–#815) (#1133)
- Isolate config reconciliation manifest assertion (#1144)
- fix: reject actor JWTs on user auth paths (#1147)
- fix: isolate app embeds from form capabilities (#1149)
- Bind Solution app headers to authorized callers (#1143)

## Fixed vulnerabilities

Security-related PR titles below are an inventory, not a verified CVE assessment. The release reviewer should add confirmed identifiers and impact before merging.

- Merge upstream worker SDK transport and credential security (#1087)
- fix: complete Main Sonar security and coverage remediation (#1101)
- fix(security): update vulnerable runtime dependencies (#1134)

## Upgrade notes

CLI/server contract: 13 -> 13. Install the CLI bundled with this release; mismatched contracts are rejected.
Before upgrading, back up the database and review the exact release's migration heads. Apply migrations using the protected infrastructure migration lane.
Automatic rollback is not certified. Preserve terminal workflow retirement evidence and admission safeguards during application rollback. Database downgrade past 20261003_workflow_retirement refuses once retirement evidence exists; use a reviewed recovery plan, not an automatic schema downgrade.
Before enabling terminal Live retirement, review actual production caller/byte evidence. The native read-only census does not prove absence of Python, operational file, App Source/dist, or external callers. Coordinate owners and retain the exact reviewed evidence digest before authorizing retirement.
Read back retirement inventory to verify retained row/evidence IDs. An uncertain outcome requires readback before any exact retry; do not replay with fresh targets. Keep the forward schema during application-image rollback.
Follow the [terminal-retirement runbook](https://github.com/Midtown-Technology-Group/bifrost/blob/08255ad355a3ab63f0c04a214c3c230432552dfe/docs/architecture/rapid-workspace-promotion.md#obsolete-registrations-in-the-same-cutover).
Contract comparison requires: no additional bump floor.
This release uses the MTG fork's independent SemVer; upstream versions are not its baseline.

## Contributors

@MTG-Thomas

## Source

Reviewed interval: `v3.0.0..08255ad355a3ab63f0c04a214c3c230432552dfe`.

Publishing packages does not deploy the MTG production runtime.
