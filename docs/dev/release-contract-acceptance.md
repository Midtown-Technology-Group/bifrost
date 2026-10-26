# Offline release contract acceptance

Independent test lane based on producer `3c92d51ef34d6fd46b4608dd163886b7789f7a12`
and consumer `52df6e8287db1ec399469b4c66c5a01392f881d5`
([infra draft PR 2119](https://github.com/MTG-Thomas/bifrost-infra/pull/2119)).
No producer or consumer implementation is changed. Fixtures are synthetic,
unsigned data; mocked signature success does not establish cryptographic trust,
CI qualification, production readiness, or deployment authorization.

Run existing suites:

```sh
python3 scripts/release/test_production_manifest.py
python3 scripts/release/test_release_manifest.py
BIFROST_INFRA_CONTRACT_ROOT=/path/to/exact/consumer-checkout \
  python3 scripts/release/test_consumer_contract.py
BIFROST_PRODUCER_CONTRACT_ROOT=/path/to/revised/producer-checkout \
  python3 scripts/release/test_revised_producer_contract.py
```

`scripts/release/fixtures/protected-main/baseline.json` contains a complete
producer-generated tuple, promotion inputs, architecture inventories and a
synthetic verification context. Git identities belong to a disposable fixture
repository, not remote main. `revised-producer.json` contains actual revised producer assembly output and
retained synthetic verification results at `723e767a7ac3c21a1b2645f7b1e033a6a12c6a22`.
`cases.json` supplies portable mutations and expected
consumer decisions. Apply dotted `manifest.*` changes to the manifest and other
consumer changes to the verification context. Producer mutations use `output`
and `receipt` component maps. These cases are inputs, not an alternative admission
implementation. The consumer integration suite exercises the actual supported
functions against the producer baseline; remaining corpus scenarios are not
automatically claimed executed. The expired-registry case describes a future
retrieval capability: this pinned consumer deliberately rejects expired Actions
artifacts even when registry evidence is available.

| Acceptance boundary | Offline evidence / disposition |
| --- | --- |
| Complete API/client/worker tuple, dev version and real architecture inventory | Existing producer tests pass with mocked candidate verification. Real consumer admission functions pass with simulated external transports; no real signatures verified. |
| Foreign producer, mixed digest/run/attempt, incomplete tuple, stable relabel | Portable mutations run through real producer assembly and reject. |
| Foreign or PR signer; issuer, subject and source binding | Real registry verifier command construction tested against simulated foreign/PR signer and wrong issuer refusals; main bundle source binding tested. |
| Same-repo valid signer, false source labels, contradictory actual candidate SLSA material | Original inherited-verifier regression remains red at `3c92d51ef`. Revised assembly at `723e767a7` rejects through its new SLSA v1 policy; 3 independent revised tests pass. |
| Actual candidate build versus main promotion identity | Candidate material must bind actual candidate commit/tree; promotion binds main commit and run. Same-tree promotion does not rewrite build origin. |
| Bare missing candidate run fields | Not evidence by itself of false-source acceptance; no claim that promotion/v1 must gain run fields. Qualify actual existing build evidence independently. |
| Rapid merges and revert restoring equal tree | Existing producer tests pin checkout/source; equal tree does not transfer source receipts. Consumer qualification accepts explicit older main and rejects old receipt reuse for equal-tree revert. Approval/runtime pinning remains unverified. |
| Expired download with verified retained/registry evidence | Pinned consumer rejects expired/absent initial artifact before download, including when registry evidence exists. Registry-only recovery is a documented capability gap, not a new defect. |
| Unknown, interrupted or superseded run/attempt | Real consumer admission rejects unknown, in-progress, cancelled, missing conclusion, superseded attempt and wrong source. Sonar is not independently proven by these fixtures. |
| Schema v2 strictness, unknown version/extra fields and stable v1 isolation | Original tuple passes; consumer rejects invalid cases. **Revised v2 tuple is incompatible:** required `candidate_provenance` is an unknown image field to pinned consumer. |
| Installed schema differs from source parent | Executed test proves parent-relative `upgrade_required: false` cannot certify installed DB compatibility. |
| Interrupted deployment and retained work | Real retained lock/bundle loading preserves bytes on verification refusal. Five existing retained-work/receipt tests pass, including refusal to reopen after unknown stop intent. No deployment transaction executed. |
| Rollback and terminal retirement | Producer warning preserved and tested. Consumer rejects rollback-supported relabel. Installed retirement/downgrade safety remains unverified; no database or production transaction used. |

The false-source regression supplies an existing BuildKit SLSA v0.2-shaped
material identifying a different commit while all expected labels and the image
subject digest match. External Docker/cosign calls are simulated, while the real
verifier runs. At this producer revision it requests only metadata and
`cosign verify`, never actual candidate provenance. The failure is
`CandidateImageError not raised`, not a missing-field assertion. The revised producer adds actual SLSA source/digest/build identity validation at
the assembly boundary rather than changing this inherited helper. This test lane
leaves both implementations unchanged and tests the new boundary separately.

## Consumer integration evidence

`test_consumer_contract.py` checks checkout HEAD and both imported consumer source
files against the exact commit before running. It imports the existing promoter
and calls only these supported functions: `validate_production_manifest`,
`validate_manifest`, `qualify_production_manifest`, `verify_attestation`,
`verify_registry_tuple`, `verified_production_manifest`, and `load_release_record`.
It does not call the promoter/deployer CLI main. All Docker/cosign/GitHub process
and HTTP seams are replaced with bounded simulations that reject unknown commands.
The actual validator, qualifier, bundle verifier command construction, registry
predicate decoder, runnable-image validation and inventory comparison execute.

Results on this host (offline, no VM):

| Command / source | Actual result |
| --- | --- |
| `BIFROST_INFRA_CONTRACT_ROOT=/tmp/bifrost-infra-contract-52df6e8 python3 scripts/release/test_consumer_contract.py` | 13 passed, 1 blocking revised-wire failure |
| `BIFROST_PRODUCER_CONTRACT_ROOT=/tmp/bifrost-producer-contract-723e767 python3 scripts/release/test_revised_producer_contract.py` | 3 passed |
| `python3 /tmp/bifrost-producer-contract-723e767/scripts/release/test_production_manifest.py` | 20 passed |
| Consumer checkout: `/home/thomas/.cache/venvs/ws/bin/python -m pytest -q tests/test_platform_release.py tests/test_promote_platform_images.py tests/test_promotion_trust_gate.py` | 176 passed |
| Consumer checkout: same pytest command on `tests/test_appservice_runtime_deploy.py -k 'retained_production_pins_require_exact_live_tuple or preserved_pin_schema_gate_uses_same_reviewed_lock or pre_stop_finalizer_will_not_reopen_after_unknown_stop_intent or deployment_receipt_is_bounded_source_and_runtime_evidence'` | 5 passed, 159 deselected |
| Producer rejection regression, selected by full unittest method name below | 1 failure, `CandidateImageError not raised` |
| Existing producer/stable suite evidence from initial fixture commit | 15 pass + 1 blocking failure / 54 pass respectively |

Original producer `3c92d51ef` cases pass at consumer `52df6e8`. The new revised-wire
failure and its minimal source difference are recorded below. The consumer owner's reported broader 959 tests and 3 signing tests (with 2 Azure CLI
Bicep skips) were not rerun or adopted as independent evidence here. No new skip,
retry, timeout extension or skip-to-green was added.

Minimal known blocking source example at producer `3c92d51ef`: candidate labels
assert the selected source/tree/version; simulated successful same-repository
signature verification binds its index digest; available SLSA material points to
`source.parent`, a different commit. Real `verify_candidate` accepts because it
never reads that material. Reproduce with:

```sh
python3 scripts/release/test_production_manifest.py \
  ProductionManifestTests.test_signed_candidate_with_false_source_labels_must_be_rejected
```

## Revised producer versus pinned consumer: blocking wire mismatch

Revised producer `723e767a7ac3c21a1b2645f7b1e033a6a12c6a22` became locally available
during acceptance. Its `production_manifest.build()` now validates real candidate
SLSA v1 via `candidate_provenance.verify()` and requires
`images.<component>.candidate_provenance`. The independent revised-producer suite
reuses its existing disposable Git fixture and `verified_results` test seam. Real
source/tree/workflow/invocation policy executes; signature verification transport
and Git fetch are simulated. Correct-label/allowed-signer evidence with a different
actual queue invocation is rejected, and mixed original candidate attempts reject.
The original inherited-helper test remains unchanged and red at its original SHA;
the new production assembly boundary passes the corresponding rejection test.

Consumer `52df6e8287db1ec399469b4c66c5a01392f881d5`,
`scripts/platform_release.py:45`, requires exactly these image keys:

```python
keys(record, "repository digest platforms")
```

The revised producer requires this additional key on API, client and worker:

```text
images.api.candidate_provenance
images.client.candidate_provenance
images.worker.candidate_provenance
```

Passing the unmodified actual revised assembly output to the real pinned
consumer raises `ValueError: Production manifest has invalid api identity/platforms`
at line 54. The regression asserts admission and remains red. A diagnostic control
removing only those three fields passes, proving the mismatch's narrow cause;
stripping provenance is **not** an admission workaround. The full minimal source
input is `scripts/release/fixtures/protected-main/revised-producer.json`.

```sh
BIFROST_INFRA_CONTRACT_ROOT=/tmp/bifrost-infra-contract-52df6e8 \
  python3 scripts/release/test_consumer_contract.py \
  ConsumerContractAcceptance.test_revised_producer_tuple_must_pass_consumer_wire_contract
```

This is a draft-contract reconciliation dependency, not a request to relax the
consumer's strictness. The consumer owner must publish a revised immutable ref
supporting the required nested proof, its retained bundle hashes and original
candidate qualification. Eddie owns that handoff. No direct owner contact occurred.
The consumer's source currently documents its lack of original candidate
qualification; fixtures do not prove CI/Sonar evidence for that invocation.

The detached consumer checkout `/tmp/bifrost-infra-contract-52df6e8` is retained
for reproducing these read-only tests. The revised producer checkout
`/tmp/bifrost-producer-contract-723e767` is likewise retained, without services. Owner files/worktrees remain untouched; no
services or containers were created. The test branch remains isolated from the
producer branch and has not been updated to a moving producer ref.

No PR is eligible while the cross-repo contract remains red. Pre-PR gates and
current origin/main integration remain required after the consumer reconciliation.
No draft PR, push, merge, replay or production change was performed.
No live stack, shared VM, credential, deployment, replay or production operation
was used. All evidence here is offline source/fixture evidence.
