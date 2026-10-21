# October 19–20, 2026 release-process incident and corrective contract

Status: recorded incident and prepared corrective design, **not an implemented fix**.
Scope: documentation only; no workflow, protection, deployment or runtime changes.
Accountable owner: the Codex release/unblock workstream in this thread.
Last verified incident snapshot: October 20, 2026, 20:58:23 EDT (October 21, 00:58:23 UTC).

## Problem and impact

A routine platform repair became an operator-supervised sequence of pre-PR,
PR, queue, main and tag validation, followed by a consumer that still waited for
the entire tag workflow after immutable publication had completed. The owner
repeatedly had to discover the next gate, challenge duplicated validation and
change bypass settings himself. This was a release-process incident even though
the published images passed their artifact checks.

The user's acceptance standard is a commit, PR and release process that a
competent engineer can explain and comfortably show another engineer. Most
merged product changes should produce releases; deployment remains a separate,
controlled operation. The process must work without continuous owner supervision.

At the snapshot:

- Stable v3.1.0 was published and immutable at 20:47:06 EDT, from
  `9c6c27d826c34294edd8396e0a658336b7df8dfe`.
- Image builds, exact-content acceptance, signatures and release publication
  had passed. Tag Sonar was still collecting comprehensive Python coverage.
- The canonical infrastructure promoter required the entire tag workflow to
  conclude successfully. The release could not yet complete its normal handoff.
- Production remained on the last independently read-back dev.319 tuple.
  Migrator, schema, retained-work and accounting holds were not resolved by
  publishing v3.1.0.
- This report does not claim that documentation prevents recurrence. Closure
  requires the implementation and end-to-end evidence below.

## Evidence and time conventions

All narrative clock times are America/Indianapolis, EDT, UTC−4. Dates are
October 19–20, 2026. GitHub URLs are the underlying PR/run receipts. A time marked
“receipt” is the comment creation time, not an inferred execution time. Workflow
end times use final GitHub `updated_at` unless a job/step completion time is
specified. Exact conversation-message timestamps were not exported; causal
conversation order is preserved without assigning fabricated seconds.

Concurrent intervals overlap. Do not sum them as wall-clock elapsed time or
claim organization-wide runner occupancy from a repository-only snapshot.
The observed five simultaneous coverage collectors belonged to five workflow
runs; they were not five phases of a single analysis.

## The dependency graph that should have been inspected first

The relevant implementation at the incident source was:

1. PR qualification and native merge-queue authorization.
2. Release recovery/preparation and exact release-head approval.
3. Exact release-merge main CI, including its comprehensive coverage collector.
4. Release-tag ownership/provenance validation and immutable tag creation.
5. Tag CI: lint, units, backend/browser E2E and MCP qualification.
6. Tag manifest validation → three final-version image builds → exact-image
   content acceptance → attestations → immutable release publication.
7. Separately, tag Sonar collection/analysis ran alongside steps 5–6.
8. The infra promoter admitted the tag only if the entire tag CI run reported
   completed/success, making step 7 a release-handoff dependency after publication.

Source anchors:

- [Platform CI at release source](https://github.com/Midtown-Technology-Group/bifrost/blob/9c6c27d826c34294edd8396e0a658336b7df8dfe/.github/workflows/ci.yml):
  `verify-release-manifest.needs` includes unit/E2E/lint/MCP, not Sonar;
  `create-release` depends on the three image builds.
- [Release-tag workflow](https://github.com/Midtown-Technology-Group/bifrost/blob/9c6c27d826c34294edd8396e0a658336b7df8dfe/.github/workflows/release-tag.yml)
  and [release operator](https://github.com/Midtown-Technology-Group/bifrost/blob/9c6c27d826c34294edd8396e0a658336b7df8dfe/scripts/release/automatic-release.py)
  verify main CI and dispatch tag CI.
- [Infra promoter](https://github.com/MTG-Thomas/bifrost-infra/blob/35df5c0e890196ba805c02ab9f6ba61c865cfd7e/scripts/promote-platform-images.py):
  `successful_run_for_sha` requires completed/success for tag runs;
  `verified_release_manifest` separately validates exact tag/source/run/attempt,
  signed manifest and image tuple.
- [Prepared collector design](2026-10-20-single-pass-sonar-coverage.md).
  Its original scope stopped short of the complete release handoff.

“Published on GitHub” was a correct asset state but an inadequate completion
criterion for cutting a usable release. The consumer dependency belongs in the
release-cutting critical path. It must not be described as an unrelated infra
problem to make release completion appear earlier.

## Wall-clock record

### Oct 19, 21:11:35 EDT — The retained-work inspection and image-pin work began

Lane: Infra. Infra #2072 opened. The objective was to obtain fresh production evidence without deployment, recovery, cleanup interruption or sealing. Inspection needed explicit inputs: workflow defaults selected a production release.

[#2072](https://github.com/MTG-Thomas/bifrost-infra/pull/2072)

### Oct 19, 21:29:31–21:30:40 EDT — The accidental default run was cancelled

Lane: Production. Run 39353712365 selected the default release path. It was cancelled with no runtime or recovery steps executed. This was not a production deployment.

[Run 39353712365](https://github.com/MTG-Thomas/bifrost-infra/actions/runs/39353712365)

### Oct 19, 21:57:56 EDT — Accounting reconciliation safeguards merged

Lane: Workspace. Workspace #1593 merged as 8c1631a. It improved safe reconciliation; merging it did not establish that previously delivered work was correctly accounted for.

[#1593](https://github.com/MTG-Thomas/bifrost-workspace/pull/1593)

### Oct 19, 22:02:00 EDT — Completed observations could survive an inspection failure

Lane: Infra. #2073 merged as 6f2e492. The inspection had been losing useful evidence when a later query failed; this repair retained completed observations.

[#2073](https://github.com/MTG-Thomas/bifrost-infra/pull/2073)

### Oct 19, 22:14:39–22:55:39 EDT — Reviewed package delivery failed after a long delivery step

Lane: Workspace. Run 39357853471 retained its intent and readback but failed. The delivery job took 40m17s and reported a container/script failure. We did not treat failure as proof of zero effects or replay it blindly.

[Run 39357853471](https://github.com/MTG-Thomas/bifrost-workspace/actions/runs/39357853471)

### Oct 19, 22:24:47 receipt EDT — The first preserved picture showed why production had to stay held

Lane: Production. The Eddie #4 receipt recorded dev.319, schema 20261006_mtg_upstream_sdk, 68 retained reservations, a cleanup callback and lease. Teams action receipts were unknown after an 8-second query deadline. Readiness was false. An earlier readiness check had separately encountered an 83-second renderer cold start against a 60-second limit.

[Eddie receipt](https://github.com/MTG-Thomas/Eddie/issues/4#issuecomment-6281874740)

### Oct 20, 07:39, recorded 07:40:37 EDT — The isolated canary compute was restored after your approval

Lane: Canary. Only app-mtg-bifrost-canary-20260920 and its B3 plan were restored from the approved preview. Runtime remained disabled, existing identity was retained, and the two approved /32 restrictions plus default Deny remained. Estimated allocation cost: $0.07/hour.

[Compute receipt](https://github.com/MTG-Thomas/Eddie/issues/4#issuecomment-6290024226)

### Oct 20, 07:43:28 receipt EDT — The two approved canary role assignments were verified

Lane: Canary. Website Contributor on the canary app and Web Plan Contributor on its plan were granted to the existing deploy principal. This did not authorize production grants, secret changes or runtime deployment.

[Grant receipt](https://github.com/MTG-Thomas/Eddie/issues/4#issuecomment-6290070822)

### Oct 20, 07:47:26 receipt EDT — Canary PostgreSQL TLS verification was repaired

Lane: Canary. Only the two approved canary database-URL secret versions changed to verify-full. Credentials and metadata were preserved. Protected read-only preflight passed; no SQL writes occurred in that preflight.

[TLS receipt](https://github.com/MTG-Thomas/Eddie/issues/4#issuecomment-6290137209)

### Oct 20, 08:07:04; 08:07:28–08:14:05 EDT — Inspection was decoupled from candidate-pin changes

Lane: Infra. #2074 merged as 4fe8715. Its production inspection passed schema preflight but failed in native inspection; runtime and recovery were skipped. A failed inspection still did not mean its unknown counts were zero.

[#2074](https://github.com/MTG-Thomas/bifrost-infra/pull/2074) · [Run 39419899004](https://github.com/MTG-Thomas/bifrost-infra/actions/runs/39419899004)

### Oct 20, 09:00:41; 09:12:36 EDT — The dev.812 pin candidate merged, then its executable-pin defect was fixed

Lane: Infra. #2072 merged as eed1a88. #2075 then merged as 6a7dad31 to distinguish executable image subjects from OCI evidence artifacts. This changed the reviewed candidate pins, not production runtime.

[#2072](https://github.com/MTG-Thomas/bifrost-infra/pull/2072) · [#2075](https://github.com/MTG-Thomas/bifrost-infra/pull/2075)

### Oct 20, 09:12:55–09:15:46 EDT — Fresh schema evidence identified the canary migration gap

Lane: Canary. Read-only run 39428494957 found current head 20261006_mtg_upstream_sdk versus image head 20261017_teams_receipt_index. This established a real schema mismatch rather than a reason to bypass the check.

[Run 39428494957](https://github.com/MTG-Thomas/bifrost-infra/actions/runs/39428494957)

### Oct 20, 09:20:01–09:25:30 EDT — Your narrowly approved canary migration completed and was read back

Lane: Canary. Run 39429484550 applied the two additive migrations: work_deliveries.started_at and the concurrent Teams-receipt index. Follow-up read-only run 39430137075 confirmed head 20261017_teams_receipt_index. Production schema was untouched.

[Run 39429484550](https://github.com/MTG-Thomas/bifrost-infra/actions/runs/39429484550) · [Run 39430137075](https://github.com/MTG-Thomas/bifrost-infra/actions/runs/39430137075)

### Oct 20, 09:28:28–09:29:35 EDT — The closed canary bootstrap preview was obtained

Lane: Canary. The reviewed main 6a7dad31 preview produced fingerprint ecbceb…f4dc. Your later authorization covered the closed bootstrap, a new preview, normal one-slot opening and one nonce-owned SDK/Blob smoke.

[Run 39430685920](https://github.com/MTG-Thomas/bifrost-infra/actions/runs/39430685920)

### Oct 20, 10:07:15; 10:09:19–10:09:20 EDT — The actual denied HTTPS egress was identified and the approved rule verified

Lane: Canary. The canary origin reported 96.75.253.105 in x-ms-forbidden-ip from node talos-qm9-7m2. After your approval, exactly one Allow rule was added: 96.75.253.105/32, priority 102, canary-talos-pve-egress. Health and frontend then returned 200. Existing rules and default Deny remained; SCM inherited the added reachability.

[Connectivity evidence](https://github.com/MTG-Thomas/Eddie/issues/4#issuecomment-6293108564)

### Oct 20, 10:09:38–10:20:53 EDT — The guarded final-open path passed

Lane: Canary. Run 39436717216 completed on main 6a7dad31 with a fresh preview. Readback established the dev.812 canary, current schema, open admissions, one worker slot, enabled scheduler and healthy routes.

[Run 39436717216](https://github.com/MTG-Thomas/bifrost-infra/actions/runs/39436717216)

### Oct 20, 10:30:32 receipt EDT — The isolated SDK/Blob qualification completed

Lane: Canary. The approved one-process DNS override resolved only the PostgreSQL hostname to verified private IP 10.30.0.132 while retaining hostname-verified TLS. The nonce-owned synthetic canary execution succeeded, including Blob round-trip/copy. No system DNS or production configuration changed.

[Smoke receipt](https://github.com/MTG-Thomas/Eddie/issues/4#issuecomment-6293274475)

### Oct 20, 10:55:06 EDT — Independent count retention merged

Lane: Infra. #2076 merged as 3efd87f. A timeout in one count no longer discarded other completed counts. This repaired the evidence path without relaxing its readiness decision.

[#2076](https://github.com/MTG-Thomas/bifrost-infra/pull/2076)

### Oct 20, 11:24:18–11:25:41 EDT — Read-only index and privilege evidence explained the next production blocker

Lane: Production. #2077 merged; its protected probe confirmed that the Teams index was absent and runtime role bifrost had direct CREATE and object ownership. Running migrations as that runtime identity would not establish the required privilege separation.

[#2077](https://github.com/MTG-Thomas/bifrost-infra/pull/2077) · [Run 39448130119](https://github.com/MTG-Thomas/bifrost-infra/actions/runs/39448130119)

### Oct 20, 11:42:50–12:46:03 EDT — The production privilege-separation plan was built and tested without applying it

Lane: Infra. #2078 documented the proposal; #2079 added disposable PostgreSQL role proof; #2080 separated runtime/operator URLs; #2081 added ownership inventory; #2082 bound the proposal to exact objects. These were source, test and read-only inventory changes—not production role provisioning or migration.

[#2078](https://github.com/MTG-Thomas/bifrost-infra/pull/2078) · [#2079](https://github.com/MTG-Thomas/bifrost-infra/pull/2079) · [#2080](https://github.com/MTG-Thomas/bifrost-infra/pull/2080) · [#2081](https://github.com/MTG-Thomas/bifrost-infra/pull/2081) · [#2082](https://github.com/MTG-Thomas/bifrost-infra/pull/2082)

### Oct 20, 12:48:57; 12:48:59–13:51:26 EDT — The item-cost/retained-handoff platform repair merged and dev.819 was published

Lane: Platform. #1360 merged as 010e376. Main CI #5264 succeeded and published 3.0.1-dev.819. A published package was now available, but production was still running dev.319.

[#1360](https://github.com/Midtown-Technology-Group/bifrost/pull/1360) · [CI #5264](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39460741655)

### Oct 20, 12:49:05–13:30:07 EDT — Another normal delivery exposed unresolved accounting

Lane: Workspace. Run 39460754972 failed. Its receipt distinguished 55 installations: 48 ambiguous/failed, four accounted and three released. This was not permission to replay the successful publication or assume the ambiguous installations had no effects.

[Run 39460754972](https://github.com/MTG-Thomas/bifrost-workspace/actions/runs/39460754972)

### Oct 20, 13:39:04; 13:39:07–14:29:07 EDT — The ZIP portability repair opened and passed full CI

Lane: Platform. #1362 made ZIP metadata deterministic across Windows and Unix producers, including create_system and mode metadata. It addressed source portability; it did not retroactively reconcile old deliveries or add permissive legacy-hash acceptance.

[#1362](https://github.com/Midtown-Technology-Group/bifrost/pull/1362) · [CI #5267](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39467931716)

### Oct 20, 13:51:07–13:56:41 EDT — The latest retained-work inspection preserved a materially non-idle state

Lane: Production. Run 39469648388 reported 69 reservations, an active cleanup callback, nonzero work/effects and schema 20261006. Teams action receipts remained UNKNOWN after timeout, so readiness was false. Native inspection failed; runtime and recovery jobs were skipped.

[Run 39469648388](https://github.com/MTG-Thomas/bifrost-infra/actions/runs/39469648388)

### Oct 20, 14:04:12 EDT — The dev.819 infrastructure proposal opened green but remained undeployed

Lane: Infra. #2083 prepared the canonical API/client/worker tuple. Its four required checks passed. You chose a new stable candidate instead of a development-release exception, including #1362 once green. The existing proposal still needs the resulting published stable tuple.

[#2083](https://github.com/MTG-Thomas/bifrost-infra/pull/2083)

### Oct 20, 15:35:06 EDT — The approved ZIP fix merged

Lane: Platform. #1362 merged as 614efe23. The stable-candidate source could now include it, as you requested.

[#1362](https://github.com/Midtown-Technology-Group/bifrost/pull/1362)

### Oct 20, 15:35:53 EDT — A stale unpublished release candidate required an explicit recovery path

Lane: Release. #1363 opened to safely supersede historical release PR #1078, whose post-merge CI had failed before publishing v3.1.0. No existing immutable v3.1.0 tag/release could simply be promoted. The recovery had to preserve the failed history and prevent the old candidate from publishing later.

[#1363](https://github.com/Midtown-Technology-Group/bifrost/pull/1363)

### Oct 20, 15:49:56–16:08:14 EDT — Production bootstrap qualification advanced; no production privileges changed

Lane: Infra. #2086 added bounded bootstrap metadata and selective function/trigger proof. A dependency advisory initially failed CI; the narrow certifi repair and the separately owned Vite repair cleared it. #2086 merged at 16:06:20 as 205f65b; read-only run 39489186663 finished at 16:08:14.

[#2086](https://github.com/MTG-Thomas/bifrost-infra/pull/2086) · [#2085](https://github.com/MTG-Thomas/bifrost-infra/pull/2085) · [Run 39489186663](https://github.com/MTG-Thomas/bifrost-infra/actions/runs/39489186663)

### Oct 20, 15:59:37–16:15:55 EDT — Some #1363 failures were genuine defects, not duplicate gates

Lane: Release. Review found a publication race: an old candidate could publish after supersession. The fix checked publication ownership before tag creation and packaging dispatch. A full test run also found five fixtures missing the new git origin setup; 14,124 tests passed. The overall run was later cancelled, but the unit failure was real.

[CI #5277](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39488152849) · [#1363](https://github.com/Midtown-Technology-Group/bifrost/pull/1363)

### Oct 20, 16:08:00.896 snapshot EDT — The bootstrap evidence showed a feasible administrator path, but no provisioned migrator

Lane: Production. The dedicated bifrost_schema_migrator role and proposed migration secret were absent. Runtime bifrost still owned the production objects. The existing administrator had elevated capabilities, but its membership in bifrost had SET/INHERIT disabled. Provisioning and qualification still needed an exact approved plan; inventory alone did not authorize grants.

[Run 39489186663](https://github.com/MTG-Thomas/bifrost-infra/actions/runs/39489186663)

### Oct 20, 16:15:17–17:02:34 EDT — The fixture repair passed full CI

Lane: Release. CI #5278 passed on 868afe4 after realistic local-origin fixtures and additional git cases. Subsequent Sonar findings caused another source revision, so this green run was not the final-head evidence.

[CI #5278](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39490392203)

### Oct 20, 17:04:14–18:04:09 EDT — The final #1363 source passed full CI and Sonar

Lane: Release. CI #5280 passed on 54e0690. Sonar’s final analysis at 18:02:41 passed with zero reported issues, 84.4% new-code coverage and zero hotspots. We established that the PR merge tree, PR head and eventual main merge had the same Git tree.

[CI #5280](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39497335165) · [#1363](https://github.com/Midtown-Technology-Group/bifrost/pull/1363)

### Oct 20, Before 18:16:57 EDT — We added avoidable validation and queue work

Lane: CI. I launched manual pre-PR validation despite already having full-CI evidence, and the one-at-a-time merge queue scheduled another overlapping CI run. You challenged that duplication. We accepted the proven identical-tree evidence for the merge decision and cancelled obsolete work rather than inventing successful statuses. Exact launch/cancellation times are not all reconstructed here.

### Oct 20, 18:16:57 EDT — You bypassed the queue and merged #1363

Lane: Release. #1363 merged as de34e07 after you added yourself as a ruleset bypass actor. My earlier override attempts had been denied. You made the repository-policy change; I did not change the ruleset.

[#1363](https://github.com/Midtown-Technology-Group/bifrost/pull/1363)

### Oct 20, 18:17:01–19:07:37 EDT — Main ran the comprehensive path again

Lane: CI. CI #5285 passed on de34e07 after another 50m36s workflow interval. Its comprehensive Python coverage step alone took about 34 minutes. The release preparation still required successful main CI under the existing implementation.

[CI #5285](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39506952510)

### Oct 20, 18:35:05 EDT — The preparation-only evidence-reuse PR opened

Lane: CI. #1365 proposed accepting trusted, successful same-tree PR evidence for release preparation, with source/attempt/Sonar checks. It did not redesign the collector or remove the publisher’s main-CI requirement. That limited scope matters: it could not eliminate the wait already occurring at publication.

[#1365](https://github.com/Midtown-Technology-Group/bifrost/pull/1365)

### Oct 20, 18:55:43 EDT — The broader collector redesign was prepared locally

Lane: CI. Signed local commit c73a5a0 contains the design: normal test shards produce coverage once; a bounded collector validates and combines their artifacts; Sonar consumes that result. It preserves coverage and runtime-evidence guards. This is a prepared design, not an implemented or merged optimization.

### Oct 20, 19:08:24–19:09:03 EDT — The new stable release PR was generated

Lane: Release. Recovery workflow 39512998876 used the successful exact-source main CI and explicitly superseded #1078. It created signed release PR #1366 at 19:08:49 for v3.1.0. Its source included the dev.819 repair plus #1362 and the reviewed release recovery; the PR itself changed release metadata/notes.

[Release preparation](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39512998876) · [#1366](https://github.com/Midtown-Technology-Group/bifrost/pull/1366)

### Oct 20, 19:15:02; 19:18:20 EDT — Your explicit approval was recorded and the candidate merged

Lane: Release. At your direction, an APPROVE review was posted transparently as your approval. Required-check enforcement still blocked the merge until you changed the admin-bypass setting. #1366 then merged as 9c6c27d at 19:18:20. The stale PR CI #5293 was cancelled after merge.

[#1366](https://github.com/Midtown-Technology-Group/bifrost/pull/1366) · [Approval receipt](https://github.com/Midtown-Technology-Group/bifrost/pull/1366#pullrequestreview-5592982910)

### Oct 20, 19:18:22–20:13:05 snapshot EDT — The release merge is undergoing full main CI again

Lane: CI. CI #5294 is running on 9c6c27d. The Sonar coverage job began at 19:19:34; “Collect comprehensive Python coverage” began at 19:30:53 and was still running at 20:13:05—42m12s in that step. The v3.1.0 tag was still absent at this snapshot.

[CI #5294](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39514127765)

### Oct 20, 19:25:42; 19:56:05 EDT — The separate evidence-reuse PR passed once, then received a narrow revision

Lane: CI. Initial #1365 CI #5292 passed, including Sonar. One repeated workflow-path literal was extracted, and its branch was refreshed onto current main as 09e5cf5. Fresh CI #5295 started at 19:56:05. This PR is not blocking publication of the already merged #1366.

[#1365](https://github.com/Midtown-Technology-Group/bifrost/pull/1365) · [CI #5292](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39509187595) · [CI #5295](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39518175304)

### Oct 20, 20:20:31 EDT — Main CI finally passed

Lane: CI. CI #5294 completed successfully on release merge 9c6c27d, including Sonar. Elapsed since that run started at 19:18:22: 1h02m09s.

[Source receipt](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39514127765)

### Oct 20, 20:20:36–20:21:07 EDT — The approved release was tagged—and another full CI cycle began

Lane: Release. The release-tag job passed in 31 seconds. It created/read back v3.1.0 at the same approved commit and dispatched tag CI at 20:21:05. A fresh round of tests and Sonar followed the successful exact-source main round.

[Source receipt](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39520728772)

### Oct 20, 20:26:24 EDT — The tag Sonar coverage job started

Lane: CI. Another Sonar Coverage Evidence job began inside tag CI 39520781340. It was not a dependency of the image-build/publication jobs, but consumed its own runner alongside them.

[Source receipt](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39520781340)

### Oct 20, 20:37:57 EDT — Comprehensive Python coverage began again

Lane: CI. The tag coverage job entered “Collect comprehensive Python coverage.” This was still running at the 20:58:23 snapshot. No additional manual test run was dispatched.

[Source receipt](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39520781340)

### Oct 20, 20:40:54–20:41:05 EDT — The tag’s test prerequisites cleared and manifest validation passed

Lane: Release. Verify Release Manifest passed in 11 seconds, allowing the three stable image builds to start. Tag Sonar was still running separately.

[Source receipt](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39520781340)

### Oct 20, 20:41:07–20:45:14 EDT — We finally built the stable images

Lane: Release. All three builds started at 20:41:07. Client finished at 20:43:05; API at 20:45:13; worker at 20:45:14. The longest of these concurrent image builds took 4m07s.

[Source receipt](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39520781340)

### Oct 20, 20:45:16–20:47:09 EDT — Artifact acceptance, signatures and immutable publication completed

Lane: Release. Create Release passed. Source signing, provenance, the stable manifest, exact-image content acceptance and manifest attestation all completed. The immutable v3.1.0 release’s published_at timestamp is 20:47:06.

[Source receipt](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39520781340/job/118971731654)

### Oct 20, 20:47:06 EDT — v3.1.0 became a real published release

Lane: Release. Published from 9c6c27d with the three stable image digests and signed manifest assets. This was 2h30m09s after #1363 merged and 26m01s after tag CI began. Those are wall-clock intervals, not summed runner time.

[Source receipt](https://github.com/Midtown-Technology-Group/bifrost/releases/tag/v3.1.0)

### Oct 20, Before 20:53:39 EDT — The next consumer revealed another Sonar dependency

Lane: Infra. The manifest checksum and trusted-workflow attestation verified. But the canonical infra promoter refused the still-in-progress tag workflow: “No successful platform workflow found … status=in_progress conclusion=pending.” Publication did not depend on tag Sonar; infra promotion depended on the entire tag run. My earlier statement that we could advance infra without waiting for tag Sonar was wrong.

### Oct 20, 20:53:39 local file timestamp EDT — The stable proposal was prepared; generated pins remained pending

Lane: Infra. The existing #2083 branch was refreshed from current main without pushing. The local proposal now names v3.1.0, its verified tuple and the existing production holds, replacing obsolete development-exception language. The promoter is waiting for the existing tag run before writing its canonical pins. No new CI or production operation was launched.

[Source receipt](https://github.com/MTG-Thomas/bifrost-infra/pull/2083)

### Oct 20, 20:58:23 snapshot EDT — Published for 11m17s, still waiting on the tag coverage collector

Lane: CI. The sole running tag job was Sonar Coverage Evidence, collecting comprehensive Python coverage. Images and immutable release publication were complete; canonical infrastructure promotion was still waiting for the parent workflow to conclude. Production remained unchanged.

[Source receipt](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39520781340)

## What failed, and what did not

### 1. Repeated execution without an end-to-end evidence contract

Several events independently scheduled comprehensive tests and coverage. The
Sonar collector ran tests that normal CI also executed, including a serial
comprehensive unit/E2E path alongside sharded normal jobs. Valid source evidence
was not generally consumable across pre-PR, PR, queue, main and tag contexts.

Equal source trees alone are not enough to fix this: event-specific versions,
build arguments, tested merge trees, lockfiles, images, coverage settings and
runtime inputs must be represented. The missing contract caused conservative
full repetition; it does not justify pretending old statuses qualify new inputs.

### 2. Local optimizations did not solve the complete process

PR #1365 admitted carefully bounded same-tree evidence for one release
preparation recovery path. It did not remove the main publication requirement,
the tag test round or the infra consumer's whole-workflow requirement.
The original collector design addressed test execution within a run and
compatible reuse across events, but did not yet specify a complete consumable
release qualification receipt.

A smaller collector or an earlier GitHub Release is not the terminal outcome.
The standard path must hand verified final artifacts to deployment preparation.

### 3. Publication and consumer admission used different completion predicates

The publisher could finish without tag Sonar; the promoter could not consume
that publication without the whole workflow concluding. This dependency was
discovered only after the operator asked to deploy the published release.

It must be resolved explicitly: either all required release qualification is
completed before declaring the release usable, or the promoter consumes a
reviewed, precise qualification receipt that proves the required source and
artifact checks. Removing a Sonar prerequisite by accident or ignoring a pending
required analysis is not the corrective action.

### 4. The steward added churn and reported incomplete conclusions

Codex launched avoidable manual pre-PR validation despite already available
full-CI evidence, treated overlapping checks as independently necessary without
first tracing the complete journey, and failed to forecast the tag round clearly.

Codex then said tag Sonar did not block publication and that infra could advance
without waiting for it. The first claim described the publisher correctly;
the second was false for the canonical promoter. The full consumer contract
should have been inspected before making the operational claim.

These were agent decisions and communication failures, not something the user
should have had to diagnose. “No more surprise approvals” does not waive an
actual human/security control; it requires discovering and explaining it before
committing to an execution path.

### 5. Actual defects must remain distinct from duplicate validation

This effort also repaired executable-image subject selection, retained
partial observations, ZIP portability, candidate publication ownership,
test-fixture origins and Sonar findings. New behavior or changed relevant
inputs legitimately required fresh evidence. A cancelled overall workflow did
not erase the unit failure that occurred before cancellation.

Canary migration, verified ingress repair and one-process DNS resolution were
explicitly authorized, bounded live operations. Production's retained work,
running cleanup, missing migration identity and schema mismatch were real
operational blockers. Their safeguards were not the source of duplicated CI
and are not to be relaxed to compensate for release-process inefficiency.

## Corrective decisions

These are the accepted design direction, not claims that repository controls
have already changed.

1. **Release most merged product changes.** Features, fixes, security repairs
   and shipped dependency changes normally produce a release. Documentation,
   test-only and unshipped tooling changes normally do not. Shipped plugin/docs
   content and build/packaging changes require an explicit product-impact
   classification; path names alone are insufficient.
2. **Qualify each required selection once for compatible inputs.** Normal
   test execution produces coverage; Sonar consumes it. Reuse requires explicit
   identity/configuration/environment equivalence, not a copied green badge.
3. **Build final-version artifacts once.** A dev image is not silently relabeled
   stable when baked versions differ. Verify final-version content, contracts,
   migration inventory, signatures and provenance against those exact digests.
4. **Make release cutting end with a consumable receipt.** Report source
   qualification, packaging and publication separately. Report a usable release
   only when the normal downstream promotion consumer can validate the receipt.
   Pending optional work must not silently block that consumer.
5. **Resume failures, not successful history.** Retain valid compatible
   producer evidence; retry the diagnosed failed stage and necessary dependents.
   A lost dispatch response requires readback before dispatching again.
6. **Keep publication and production rollout separate.** Release frequency does
   not authorize production migrations, callbacks, access changes, customer
   publications or schedule activation.
7. **Preserve existing controls until a reviewed cutover.** No fake statuses,
   ignored failures, ad hoc cancellation of dependency jobs, moved tags or
   replacement immutable assets. Exact-head human release approval and current
   merge/security controls remain active until any policy change is explicitly
   reviewed and authorized.

Halo CLI is useful inspiration for a direct, resumable exact-tag → package →
installation-artifact → distribution path. Its current release workflow also
reruns pytest, so it is not evidence that duplication has already been solved
there. Borrow the resume/readback behavior, not every existing gate.
See [Halo release procedure](https://github.com/Midtown-Technology-Group/halocli/blob/main/RELEASE.md)
and [release workflow](https://github.com/Midtown-Technology-Group/halocli/blob/main/.github/workflows/release.yml).

## Owned implementation slices

This workstream owns carrying these slices through reviewed implementation and
verification. No new external owner acceptance is asserted by this document.

| ID | Owner/surface | Required outcome | Current status |
| --- | --- | --- | --- |
| R1 | This workstream, platform CI | Instrument existing producers; preserve slow units, runtime process coverage and the full denominator; replace duplicate collector execution with bounded aggregation and analysis | Prepared design; not implemented |
| R2 | This workstream, platform release | Produce a complete exact-source qualification receipt consumable by tag packaging; rerun only genuinely changed version/build-sensitive acceptance | Required design extension; not implemented |
| R3 | This workstream, platform + infra promoter | Define one explicit release qualification/admission predicate; verify final manifest, source, producer identities and required quality results without depending on unrelated parent-workflow state | Required design extension; not implemented |
| R4 | This workstream, release operator/status output | Show the entire remaining critical path before dispatch, plus timestamped stage/producer identity and usable-release state | Required; not implemented |
| R5 | This workstream, release classification | Implement reviewed product-impact classification so most product changes release and non-product changes avoid unnecessary release cycles | Direction accepted; exact policy implementation pending |
| R6 | This workstream, recovery | Preserve successful compatible stages, stop stale/orphan runs through supported cancellation, and read back uncertain dispatch/publication effects | Existing behavior partly available; end-to-end proof required |

These slices extend existing scripts, workflows and evidence schemas. Do not
introduce another deployment framework, approval layer, runner fleet or service.
Keep the current release and production operations untouched while preparing
the changes. Do not push the documentation merely to create another CI run
during the present release wait.

## Regression and end-to-end acceptance contract

Implementation is not complete until the following deterministic scenarios are
tested at the affected public seams. “Checks green” alone is insufficient.

| ID | Scenario | Required observable result |
| --- | --- | --- |
| A1 | PR → merge group → main → tag have compatible tested inputs | The planned test inventory executes once per compatible producer set; consumers retain producer IDs and equivalence receipts; a required new analysis scans retained reports without executing tests |
| A2 | Same tree but different baked version, dependency, workflow, test image, coverage config or feature input | Reject incompatible reuse; name the changed input and run only the reviewed applicable qualification; never falsely transfer success |
| A3 | Synthetic PR merge tree differs from analyzed head/base | Reject mismatched reports before scan; no relabeling of source or hidden second full collector |
| A4 | Publisher completes while unrelated parent-workflow work is pending | Under the reviewed receipt contract, promoter acceptance depends on explicit required evidence, not unrelated aggregate status; if a required quality result is pending, report release qualification pending before calling the release usable |
| A5 | Required Sonar/quality result missing, pending, failed, stale, wrong project/head/base or untrusted | Refuse qualification/admission. No waiver through a published badge or receipt self-assertion |
| A6 | Final images build successfully but have wrong versions/contracts/schema heads, digests or attestation subject | Reject before release handoff; do not certify application readiness from a successful build alone |
| A7 | Worker/API/scheduler runtime report absent or empty while units are nonempty | Refuse aggregation; preserve runtime audit and uncovered authored-source denominator |
| A8 | Producer set misses or duplicates a shard, changes artifact bytes, or mixes incompatible attempts | Refuse before analysis; successful compatible retained producers keep their original attempt identities |
| A9 | Tag creation, dispatch or response fails halfway through | Read back tag and existing run; resume exactly once without moving tag, duplicating packaging or replacing published assets |
| A10 | One infrastructure job fails after other compatible producers pass | Preserve receipts and original failure; rerun the diagnosed failed stage plus required dependents, with no full-successful-suite replay |
| A11 | Product fix vs documentation/test-only vs shipped plugin content | Explain release/no-release classification; release the product fix; avoid a release for truly non-product edits; do not miss shipped content |
| A12 | Release candidate is published while production schema/retained work remain blocked | Release receipt is usable for preparation; production execution remains denied under its separate exact gates |
| A13 | Cancellation occurs during startup, test, export or publication | No task-owned resource leaks; incomplete evidence cannot satisfy a gate; uncertain effects are resolved by readback |
| A14 | Steward announces readiness or the next action | Output includes exact source, passed/pending stages, complete consumer dependency and smallest next action; no unseen full validation round appears after “ready” |

For the qualification candidate, record stage queue/run time, suite execution
count, runner-minutes, source/build/config identities and every consumer
decision. Include artifact transfer and cleanup cost. Set numeric performance
budgets from measured evidence; do not invent a promised duration.

The end-to-end rehearsal must reach the canonical infra promotion consumer.
Do not stop at a successful Sonar scan, merged optimization PR, GitHub tag or
published Release. Use fixtures and already-retained compatible evidence to
avoid launching the old and new full collectors alongside each other.

## Reporting contract and closure

Use these explicit states, with a wall-clock timestamp and immutable identity:

- Committed locally / pushed / PR-qualified / merged.
- Source qualified, with exact tested tree and relevant inputs.
- Final images built and accepted.
- Immutable assets published.
- Release qualification receipt accepted by the standard promotion consumer.
- Infra candidate reviewed/merged.
- Target preview/schema/drain passed, target deployed and runtime verified.

“Published” does not mean “deployed.” “All required checks passed” does not mean
the entire workflow is finished. An unknown effect count does not mean zero.

This incident remains **open for corrective implementation** until R1–R6 and
their applicable acceptance cases have evidence, the operator runbook states
the complete path, and one end-to-end release reaches canonical promotion
without duplicate compatible suite execution or a surprise consumer wait.
Documentation, a source-only review or a bypassed merge is not closure.

Verification for this documentation revision is limited to local link/path,
timeline and diff checks. No application tests, manual workflow dispatch or CI
run is required to write the incident down. No claim is made that the proposed
contracts or regression cases are implemented.
