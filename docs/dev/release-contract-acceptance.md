# Offline release contract acceptance

Independent test lane based on producer `3c92d51ef34d6fd46b4608dd163886b7789f7a12`.
No producer or consumer implementation is changed. Fixtures are synthetic,
unsigned data; mocked signature success does not establish cryptographic trust,
CI qualification, production readiness, or deployment authorization.

Run existing suites:

```sh
python3 scripts/release/test_production_manifest.py
python3 scripts/release/test_release_manifest.py
```

`scripts/release/fixtures/protected-main/baseline.json` contains a complete
producer-generated tuple, promotion inputs, architecture inventories and a
synthetic verification context. Git identities belong to a disposable fixture
repository, not remote main. `cases.json` supplies portable mutations and expected
consumer decisions. Apply dotted `manifest.*` changes to the manifest and other
consumer changes to the verification context. Producer mutations use `output`
and `receipt` component maps. These cases are inputs, not an alternative admission
implementation. No consumer scenario is claimed executed by this suite.

| Acceptance boundary | Offline evidence / disposition |
| --- | --- |
| Complete API/client/worker tuple, dev version and real architecture inventory | Existing producer tests pass with mocked candidate verification. Full signed-main admission awaits consumer. |
| Foreign producer, mixed digest/run/attempt, incomplete tuple, stable relabel | Portable mutations run through real producer assembly and reject. |
| Foreign or PR signer; issuer, subject and source binding | Consumer fixture inputs only; needs consumer verification seam. |
| Same-repo valid signer, false source labels, contradictory actual candidate SLSA material | **Blocking red regression** through real inherited `verify_candidate`; it accepts the image without reading provenance. |
| Actual candidate build versus main promotion identity | Candidate material must bind actual candidate commit/tree; promotion binds main commit and run. Same-tree promotion does not rewrite build origin. |
| Bare missing candidate run fields | Not evidence by itself of false-source acceptance; no claim that promotion/v1 must gain run fields. Qualify actual existing build evidence independently. |
| Rapid merges and revert restoring equal tree | Existing producer tests pin checkout/source; equal tree does not transfer source receipts. Consumer approval pinning remains pending. |
| Expired download with verified retained/registry evidence | Fixture expects exact subject/predicate verification before admission; no mutable tag fallback. Missing all evidence rejects. Retrieval adapter pending. |
| Unknown, interrupted or superseded run/attempt | Consumer cases reject; a signed tuple alone cannot qualify CI/Sonar. |
| Schema v2 strictness, unknown version/extra fields and stable v1 isolation | Fixture cases supplied; strict consumer schema validation pending. Existing stable suite remains green. |
| Installed schema differs from source parent | Executed test proves parent-relative `upgrade_required: false` cannot certify installed DB compatibility. |
| Interrupted deployment and retained work | Consumer case requires retain-and-stop, preserving selected tuple, approvals and receipts; no automatic replay. |
| Rollback and terminal retirement | Producer warning preserved and tested. Consumer must refuse unsafe automatic downgrade against installed/retained evidence. |

The false-source regression supplies an existing BuildKit SLSA v0.2-shaped
material identifying a different commit while all expected labels and the image
subject digest match. External Docker/cosign calls are simulated, while the real
verifier runs. At this producer revision it requests only metadata and
`cosign verify`, never actual candidate provenance. The failure is
`CandidateImageError not raised`, not a missing-field assertion. The producer
owner must validate actual candidate SLSA source/digest/build identity; this test
lane deliberately leaves that implementation unchanged.

Consumer dependency: an immutable published consumer ref and documented offline
entrypoint accepting manifest, signature/provenance, exact CI/Sonar qualification,
retrieval outcomes and installed/retained state. Local infra main inspected at
`2891b7f6`; no published v2 consumer ref was supplied or found in that checkout.
The consumer thread is still implementing according to the task brief. Consumer
end-to-end acceptance remains blocked until Eddie supplies that ref/entrypoint;
this lane has made no direct owner contact.

No PR is eligible while the rejection regression is red. Pre-PR gates and current
origin/main integration remain required after the owner fix and consumer handoff.
No live stack, shared VM, credential, deployment, replay or production operation
was used. All evidence here is offline source/fixture evidence.
