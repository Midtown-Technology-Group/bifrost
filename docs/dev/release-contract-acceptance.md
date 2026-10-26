# Offline release contract acceptance

Current exact pair:

- Producer: `723e767a7ac3c21a1b2645f7b1e033a6a12c6a22`.
- Consumer: `ecea7f7f07a0c79041cb9d7bb12987862b47ecf4`,
  [infra draft PR 2119](https://github.com/MTG-Thomas/bifrost-infra/pull/2119).

All fixtures are unsigned synthetic data. Simulated successful signature
verification is not real signing, CI/Sonar qualification, deployment authorization
or production proof. Owner implementations remain unchanged. The isolated test
branch incorporates the exact producer revision; its diff against that revision
contains tests, fixtures and this acceptance record only.

## Reproduce

```sh
python3 scripts/release/test_production_manifest.py
python3 scripts/release/test_release_manifest.py
BIFROST_INFRA_CONTRACT_ROOT=/path/to/exact/consumer-checkout \
  python3 scripts/release/accept_consumer_contract.py
BIFROST_PRODUCER_CONTRACT_ROOT=/path/to/exact/producer-checkout \
  python3 scripts/release/test_revised_producer_contract.py
```

The consumer acceptance runner requires `BIFROST_INFRA_CONTRACT_ROOT`, pointing
to an already-authorized external checkout at the exact consumer commit. It checks
HEAD and each imported file against that checkout's Git objects. Private consumer
source is not bundled, fetched or exported by public CI. Normal public collection
therefore does **not** execute or prove consumer admission. This limitation is
explicit, not an unconditional skip or a synthetic replacement validator.

The public producer suite can use its pinned public-source fixture when no
producer checkout variable is supplied; its archive and included file hashes are
verified before import. Both suites retain their real owner policy functions in
explicit configured runs.

They use the existing unittest framework and
owner test seams. No promoter/deployer CLI main executes. Docker/cosign/GitHub
transports are bounded simulations; unexpected commands fail closed. Revised
producer Git comparisons are real, with network fetch suppressed.

The consumer's real manifest validator, qualifier, registry decoder, runnable
image/inventory checks, retained-record loader and candidate-proof policy execute.
For each component, the actual retained `attestation.bundle` is passed to a fresh
simulated `gh attestation verify` invocation. The simulation checks inner-bundle
content and subject/source/signer constraints; real consumer policy reads its fresh
output. Cached `verificationResult` fields cannot qualify the result.

## Acceptance matrix

| Boundary | Current offline result |
| --- | --- |
| Complete immutable tuple and real baked dev version | Pass through real consumer admission; no tag resolution or stable relabel. |
| Required proof fields and coherent original invocation | All ten fields required; coherent per-component but mixed attempts rejected. |
| Foreign producer, mixed promotion digest/run/attempt, partial tuple | Producer/consumer negative cases reject. |
| Foreign or PR main signer; wrong issuer | Actual verifier command constraints exercised against simulated refusals. |
| Same-repo signer and correct labels masking false build source | Preserved producer rejection test passes at revised assembly boundary with real inherited label verification and real SLSA v1 policy. |
| False fresh candidate source/material/signature | Consumer rejects despite matching manifest and plausible cached results. |
| Cached verificationResult and inner bundle | Contradictory cached claims cannot control admission; substituted inner bundle fails signed hash. All three inner bundles are reverified. |
| Candidate build identity versus main promotion | Candidate invocation/run/attempt retained separately from main build; wrong candidate tree rejects. |
| Rapid merge and equal-tree revert | Explicit selected source stays pinned; old receipt cannot be relabeled as a revert. |
| Expired or unknown initial artifact | Rejects before download; registry-only recovery remains deliberately unsupported. |
| Unknown, in-progress, cancelled or superseded main attempt | Rejects. These fixtures do not independently prove applicable Sonar evidence. |
| Schema v2 and stable v1 separation | Revised wire case passes; stripped proof, v1/v3, unknown fields, invalid compatibility and stable relabel reject. |
| Parent-relative versus installed schema | Test proves parent `upgrade_required: false` does not certify installed DB compatibility. |
| Retained receipt and interrupted work | Lock/bundle/proof loading passes; verification refusal preserves bytes. Existing unknown-stop test refuses automatic reopen. No deployment transaction executed. |
| Rollback/retirement | Unsupported automatic rollback warning retained; rollback-supported relabel rejects. Installed DB downgrade safety remains unverified. |

Bare missing run fields were never treated as proof of false-source acceptance.
The original defect was acceptance without reading actual build provenance. The
revised producer adds a separate assembly policy that checks existing SLSA v1
source, invocation, digest, signer and workflow evidence; the inherited helper
alone remains insufficient. The preserved rejection test now exercises that real
assembly boundary rather than demanding that the helper itself implement it.

## Results and history

Host: `codex-remote-02`. Test worktree:
`/home/thomas/.t3/worktrees/bifrost-platform/release-contract-acceptance`.
No VM, live stack, service or container was created.

| Source / command | Actual result |
| --- | --- |
| Explicit `accept_consumer_contract.py`, consumer `ecea7f7f` | 18 passed |
| Current producer suite, including preserved rejection assertion | 23 passed |
| `test_revised_producer_contract.py`, exact producer `723e767a7` | 3 passed |
| Existing stable `test_release_manifest.py` | 54 passed |
| Consumer checkout: `/home/thomas/.cache/venvs/ws/bin/python -m pytest -q tests/test_platform_release.py tests/test_promote_platform_images.py tests/test_promotion_trust_gate.py` | 200 passed |
| Same pytest runner on `tests/test_appservice_runtime_deploy.py -k 'retained_production_pins_require_exact_live_tuple or preserved_pin_schema_gate_uses_same_reviewed_lock or pre_stop_finalizer_will_not_reopen_after_unknown_stop_intent or deployment_receipt_is_bounded_source_and_runtime_evidence'` | 5 passed, 159 deselected |
| Original producer `3c92d51ef`, acceptance commit `fa090f0c8` | 15 passed, 1 false-source rejection failure (`CandidateImageError not raised`) |
| Producer `723e767a7` / consumer `52df6e8`, acceptance commit `55d3b0c30` | 13 passed, 1 wire failure: `Production manifest has invalid api identity/platforms` |
| Same preserved wire test / consumer `ecea7f7f` | Pass; consumer now requires and validates candidate_provenance. |

No remaining mismatch was observed in the executed current-pair cases. The
consumer owner's reported 983 tests and two Azure CLI skips are separate evidence;
that broader suite was not independently rerun. No skip, retry or timeout increase
was added. No live signed publication, installed schema, production rollback,
CI/Sonar evidence or interrupted-deployment transaction is claimed verified.

Historical fixtures remain intact: `baseline.json` is original producer output;
`revised-producer.json` preserves the original revised-wire reproduction;
`cases.json` preserves portable scenarios. `aligned-producer.json` is freshly
emitted by the exact revised producer using its existing verified-results seam,
with a synthetic gh wrapper and inner bundle. Changing that unsigned fixture shape
is not a cryptographic fix or an owner implementation change.

## Publication correction and current limitation

[Draft #1401](https://github.com/Midtown-Technology-Group/bifrost/pull/1401) remains
stacked on producer #1400, unmerged, with no deployment authority. Commit
`756310d05` and its earlier red/green evidence remain in branch history.

The fixture correction in `4ca33e571` improperly bundled private consumer source
in this public repository. This normal corrective commit removes that archive
and its consumer index entry from the current tree. It does not remove prior
Git objects, caches, clones or historical access; no history rewrite, visibility
change or permission change is authorized here. Those decisions remain with
Thomas. No claims about readership or downstream copies have been established.

The original exact-head CI run 40415661642 passed Pre-PR Candidate and lint but
failed Sonar's repository-tool coverage because consumer class setup required
an absent external checkout. The private-source archive is not an acceptable fix.
The consumer suite is now the explicitly invoked `accept_consumer_contract.py`
runner and fails clearly without `BIFROST_INFRA_CONTRACT_ROOT`; ordinary public
collection retains the producer and public fixture-integrity tests. It does not
claim to run external consumer acceptance. No checks, coverage exclusions,
credentials/grants or workflow selectors changed.

An explicitly configured authorized local consumer checkout still executes all
18 independent cases with real consumer functions and simulated transports.
The producer fixture remains sourced solely from the public platform commit.
Its archive provenance is recorded in `sources.json`; reproduce that public
archive using the listed sorted file paths and commit with `git archive`, then
Python 3.13 `gzip.compress(tar_bytes, mtime=0)`. Never add the private consumer
repository to the source-fixture index or publish its source to repair CI.

The earlier 368-test bundled-source run is historical evidence for the removed
approach, not current public consumer-admission evidence. Fresh CI remains
unverified for this correction. The existing GitHub API quota limitation has not
been worked around using another account or token.

Detached read-only checkouts `/tmp/bifrost-infra-contract-ecea7f7` and
`/tmp/bifrost-producer-contract-723e767` are retained for reproduction; the earlier
`/tmp/bifrost-infra-contract-52df6e8` checkout retains old-pair evidence. Owner
worktrees remain untouched. Skills used: `mtg-engineering-flow` and
`github-pr-stewardship`, version `2026-10-26.1`, plus repository testing guidance.
