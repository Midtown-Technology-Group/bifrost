# Runtime evidence characterization fixtures (C1-Q1)

This tests-only package freezes the **captured expected bytes and Python input
types** from selected synthetic source-helper calls. The architect ratified
those observations as characterization fixtures. The `Python*V1` profile names
and the artifact's `bifrost.synthetic-canonical-source-probe/v1` label are
provisional research labels, not negotiated capabilities, public contracts, or
a complete execution protocol. They do not extend the sibling C1-P0 control
profile.

[evidence-vectors.json](evidence-vectors.json) is the original capture, preserved
byte for byte. It contains **64 vectors: 61 encoded outcomes and three expected
deployment non-finite exceptions**, with zero import/helper barriers. Its SHA256
is `1be1929bca57e1204a33bbad9ff2d15338adf9f4862ce26b13280917c19d514a`.
The original capture procedure was `mtg-engineering-flow/2026-09-30.1` on
2026-10-01. The repository package base is
`9f35278f90ba3757318ac9f07895bda9957330de` (#1015); that base is distinct from
the historical producer source refs below.

## Captured provenance

The original capture ran in the supported dedicated `pve-t340` Docker lane
(`pve-t340.infra.midtowntg.com`; repository access target
`pve-t340.netbird.cloud`), with these recorded inputs:

| Input | Observed pin |
| --- | --- |
| Platform producer source ref | `ba783472b770291e612433ad7ca9564fe671dade` |
| Actual mounted foundation checkout | `/home/thomas/src/bifrost-rust-core-integration` at `a5e129ac3cbcb1f3098e7dd0923721fccc84cb16` |
| Authored workspace checkout | `/home/thomas/src/bifrost-workspace-rust-mvp-baseline` at `83c1cb034dbbcfa29eb1723506735b13b4788536` |
| Authored text path | `features/utilities/workflows/check_integration_readiness.py` |
| Authored text SHA256 | `f49b1b935ef2467f66eccf3c4b2750773f664d1f0e41f8ff6b08e3f11035a1de` |
| Docker image ID | `sha256:1ed49b5898986723c040dab6cf98f05951e71a57c226e025d353f15b300ca581` |
| Python / Pydantic / pydantic-core | `3.14.7` / `2.13.3` / `2.46.3` |
| SQLAlchemy / cryptography | `2.0.49` / `50.0.0` |

The mounted foundation checkout was clean; its `api/src`, `api/shared`, and
`api/bifrost` bytes matched the platform ref. The artifact correctly retains
both the platform ref and the actual `a5e129...` mounted-foundation ref; moving
the probe into this repository does not rewrite that provenance. Six imported
producer module hashes and the authored-text hash are recorded under `sources`.
Those seven files are not a hash inventory of the entire transitive import
closure, an immutable dependency guarantee, or a deployed runtime observation.

The original probe's SHA256 was
`16e4c1a2d7d0b6231ce857af40f16e548173d8393958bf59993ccacd19ce3c0c`.
The repository [capture script](../../../../scripts/ci/capture-runtime-evidence.py)
preserves its synthetic inputs and producer calls and adds explicit CLI paths
and fail-closed source/package/environment checks before producer imports.
The repository script is therefore a guarded reproduction of the original,
not a byte-identical copy of that script.

## Separate serialization domains

| Provisional profile | Vectors | Captured seam |
| --- | ---: | --- |
| `PythonDeploymentEvidenceV1` | 27 | Raw dictionary `deployment_manifest.canonical_json`, including three exceptions |
| `PythonDeploymentDocumentV1` | 8 | Model projection through the same helper |
| `PythonWorkspaceIdentityV1` | 25 | `workspace_release.canonical_digest` |
| `PythonWorkspaceManifestV1` | 1 | `workspace_release.workspace_manifest_id` leaf-prefix normalization |
| `PythonAttemptPolicyV1` | 2 | `execution.attempts._policy_digest` |
| `PythonDeliveryPlaintextV1` | 1 | Plaintext passed by `work_delivery_store._encrypted` to encryption |

Each vector names its producer path, function, line, and source ref. Inputs are
typed descriptions: integers use decimal strings, floats include their exact
big-endian IEEE754 binary64 bits and Python `repr`, and object `entries` preserve
insertion order. Boolean and null have distinct kinds. This representation is a
fixture description, not a proposed wire schema or a second serializer.
Digest-only helpers additionally record the actual serialized input and
`json.dumps` options. Consumers must preserve those distinctions before
comparing bytes; rebuilding inputs through a generic JSON-number projection
can erase the behavior under test.

Deployment, workspace identity, attempt policy, and delivery plaintext are
different domains. Deployment sorts compact keys and emits raw UTF-8. Workspace
identity has its own non-finite behavior and manifest leaf normalization.
Attempt policy sorts compact JSON but escapes non-ASCII characters. Delivery
plaintext uses insertion order, default spaces, and ASCII escapes. Treating
these as one universal canonical encoder would lose observed differences.

The workspace/attempt helpers return hashes rather than bytes. A delegating,
tests-only `json.dumps` observer records the real stdlib result while preserving
its arguments and return value; the probe checks that the returned helper digest
matches those bytes. Delivery's real `_encrypted` performs encryption with a
publicly synthetic fixture key, but the observer exports only its plaintext.
Randomized ciphertext and key material are absent from the artifact. The
SHA256/digest fields identify these captured bytes; they establish neither a
security protocol nor Rust/Python cryptographic interoperability.

## Observed distinctions and limits

- Integer `0`, float `0.0`, and float `-0.0` have separate raw-input vectors and
  byte/hash outcomes. A default `WorkflowRegistrationControls().value` is a
  Python integer, but its JSON model projection emits `0.0`. Explicit integer
  zero validates to a float; negative zero retains its sign. The synthetic AST
  compiler definition and constructed queue helper also emit float `0.0`.
- Deployment model projection omits absent optional identity fields and empty
  named root/resource/shared-table fields. It retains nested dictionary nulls.
  Raw dictionaries retain present-null keys, so a missing key differs from a
  null key. This is model-specific projection, not a general null-elision rule.
- Binary64 cases include exponent thresholds, the smallest subnormal, largest
  finite value, and a rounding-sensitive value. For example, deployment
  `1e-5` emits `1e-05`. The finite sample is characterization, not proof of all
  integer/float magnitudes or a ratified cross-language number formatter.
- Unicode composition is preserved: precomposed `é` and decomposed
  `e` + combining acute produce different bytes. Key order, astral characters,
  private-use U+E000, controls, and U+2028/U+2029 are captured. Arrays retain
  order; ASCII escaping differs across profiles. There is no Unicode
  normalization or universal sorting rule implied for other domains.
- Deployment rejects NaN and both infinities with `ValueError`. Workspace
  identity emits `NaN`, `Infinity`, and `-Infinity` and hashes those bytes.
  These three workspace outcomes are source characterization only and remain
  **unsupported by a selected finite-JSON protocol**. The deployment exceptions
  are expected outcomes; an unexpected import/helper barrier still fails.

The selected-A-shaped recipe uses fixed synthetic UUIDs, the actual pinned
authored bytes, and the source-owned AST compiler. The authored file is read as
bytes and AST only, never imported or executed. The queue evidence comes from a
constructed `PinnedWorkflowRuntime` with synthetic bundle/manifest hashes. No
installation, database resolver, authoritative deployment closure, or durable
execution pin was consulted. This is **not installed Scenario A or B pin proof**.

This package supplies no Rust encoder, Python runtime extraction, SDK change,
Prepare/Prepared/Result protocol, schema extension, credentials, auth policy,
ownership receipt or effect execution. Architect-owned CI wiring only adds the
named stacked base `rust/runtime-contract` to existing triggers; job commands,
timeouts and gates stay intact. Those acceptance
gates remain separate. Nothing here proves full workload execution, admission,
custody, cancellation safety, delivery settlement, or a live deployment.

## Guarded reproduction

Run only in the same authorized supported Docker lane, using an existing image
with the exact ID above. The script does not launch Docker, access the network,
open a database session, invoke subprocesses, or obtain credentials. It rejects
network connect/bind/DNS and process-launch audit events as defense in depth;
the outer `--network none` and read-only mounts remain required. Importing the
source-owned helpers is part of this test; importing authored Python is not.

The caller must verify the actual image ID, checkout refs, clean mounted paths,
and source equivalence with Docker/git before execution. Script constants or
CLI arguments do not independently prove an image or git ref. For the original
mounts, inspect `git rev-parse HEAD` and `git status --short` in both checkouts,
and require this diff to be empty:

```sh
git -C /home/thomas/src/bifrost-rust-core-integration diff \
  ba783472b770291e612433ad7ca9564fe671dade \
  a5e129ac3cbcb1f3098e7dd0923721fccc84cb16 -- api/src api/shared api/bifrost
docker image inspect --format '{{.Id}}' \
  sha256:1ed49b5898986723c040dab6cf98f05951e71a57c226e025d353f15b300ca581
```

The script requires explicit source/text paths and checks all six producer-file
hashes, the authored hash, the observed Python/package versions, and the exact
five synthetic `BIFROST_*` inputs below before importing a producer. Additional
`BIFROST_*` variables fail closed. There are no alternate-pin or skip switches;
drift needs a separately reviewed capture, never an overwrite of these fixtures.
Errors omit environment values. Source hashes validate the selected producer
files, while the caller owns the broader mounted-source audit.

From the `rust/runtime-evidence-vectors` worktree on that host:

```sh
docker run --rm --pull never --network none --entrypoint /usr/bin/env \
  --mount type=bind,src=/home/thomas/src/bifrost-rust-core-integration/api/src,dst=/app/src,readonly \
  --mount type=bind,src=/home/thomas/src/bifrost-rust-core-integration/api/shared,dst=/app/shared,readonly \
  --mount type=bind,src=/home/thomas/src/bifrost-rust-core-integration/api/bifrost,dst=/app/bifrost,readonly \
  --mount type=bind,src=/home/thomas/src/bifrost-rust-evidence-vectors/scripts/ci/capture-runtime-evidence.py,dst=/probe/probe.py,readonly \
  --mount type=bind,src=/home/thomas/src/bifrost-workspace-rust-mvp-baseline/features/utilities/workflows/check_integration_readiness.py,dst=/probe/check_integration_readiness.py.txt,readonly \
  -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 \
  -e BIFROST_ENVIRONMENT=testing -e BIFROST_WORK_DELIVERY_BACKEND=postgres \
  -e BIFROST_SECRET_KEY=runtime-evidence-synthetic-fixture-key-0001 \
  -e BIFROST_DATABASE_URL=postgresql+asyncpg://probe:probe@127.0.0.1:1/probe \
  -e BIFROST_REDIS_URL=redis://127.0.0.1:1/15 \
  sha256:1ed49b5898986723c040dab6cf98f05951e71a57c226e025d353f15b300ca581 \
  -u BIFROST_VERSION python -B /probe/probe.py --source-root /app \
  --authored-source /probe/check_integration_readiness.py.txt \
  > /tmp/bifrost-runtime-evidence-replay.json \
  2> /tmp/bifrost-runtime-evidence-replay.log
cmp contracts/runtime/v1/evidence/evidence-vectors.json \
  /tmp/bifrost-runtime-evidence-replay.json
test ! -s /tmp/bifrost-runtime-evidence-replay.log
```

The key is a public test literal, not a real credential. Dummy loopback DB/Redis
URLs are non-working fixtures; no service call is needed or permitted. A
first guarded replay refused the pinned image's inherited `BIFROST_VERSION`
build label before producer imports. The launch above explicitly removes only
that image metadata variable, preserving the script's exact five-input check;
it does not relax the guard or strip arbitrary environment credentials.
Docker image identity remains externally verified by its immutable ID. A
successful replay must exit zero, have empty stderr and match the entire
artifact byte for byte, including typed inputs, options, source metadata,
observations and expected exceptions. Do not run this probe or pytest against
host platform packages to claim supported evidence. Source review, pinned
Docker replay and clean-candidate pre-PR/CI checks remain distinct evidence;
this package's original capture alone does not prove a new candidate passed.

Root replay on 2026-10-01 completed with exit zero, empty stderr and a
byte-identical full artifact (SHA256 above), using the recorded source/ref/image
pins, network disabled, read-only filesystem/mounts and UID 1000. This proves the
guarded script reproduces the selected source helpers in that supported image.
It is not Rust encoding or installed workload evidence. The initial rejected
inherited build-label attempt remains a negative pre-import guard observation.

## C1-Q2 tests-only Rust candidate

The existing `bifrost-contracts` 0.1.0 crate has an isolated encoder under
`tests/support/evidence_encoding`, its `tests/evidence_encoding.rs` consumer,
and the `runtime_evidence_vectors` test example. None is exported by the library
or called by production code. Typed integer/f64 inputs, signed zero, recursive
key sorting and distinct ASCII/insertion-order fixture writers remain separate.
The finite entry point rejects the historical workspace non-finite inputs; a
tests-only source witness renders their recorded tokens. Model adapters cover
the selected classes and empty shared-table fixture only; unknown model fields,
classes or unobserved grant content fail.

Main `f10da7c27568e65ee36d5699b72ef3a44ea7a3d8` (#1007) changes the recipe and
manifest module files for shared-table scopes. Source inspection found the
serializer, captured model fields/defaults and compiler body unchanged for
these empty-table inputs; that is source inference, not a fresh runtime capture.
The historical source hashes and compiler line 294 remain intact here. The
guarded capture script rejects the two changed producer files before imports.

After approval, run the Rust checks only in hosted CI or verified VM106 with the
canonical artifact supplied read-only. An explicit missing
`BIFROST_RUNTIME_EVIDENCE_VECTORS` path fails; the default is this repository's
one canonical file. From the Rust workspace inside that supported lane:

```sh
cargo test --locked -p bifrost-contracts --test evidence_encoding
cargo run --locked -p bifrost-contracts --example runtime_evidence_vectors -- emit
cargo run --locked -p bifrost-contracts --example runtime_evidence_vectors -- encode \
  < requests.ndjson > candidate.ndjson
```

`emit` returns `{schema,results}` with exactly 64 actual byte/error outcomes and
no echoed expected hashes. `encode` reads one JSON request per line and emits
one response per line. Requests contain `id` (1..128 ASCII token characters),
`profile` (`finite_utf8`, `sorted_ascii_fixture`, `delivery_ascii_fixture`, or
`workspace_source_fixture`), and typed `input` rooted at object `entries`.
Float descriptions use `bits_be_hex`; optional `repr` is ignored provenance.
Success returns `{id,outcome:"encoded",utf8,hex}`; failure returns
`{id,outcome:"error",error}` with a static category. Malformed/oversized records
have null IDs. The request-line cap is 8 MiB; encoded bytes are capped at 1 MiB.
These and the depth/node/member/text limits are research safety bounds, not
protocol/workload guarantees. Bigints, lone surrogates, generalized model/error
behavior and production acceptance of sampled finite-number compatibility
remain unresolved. The pinned Python peer independently hashes Rust-returned
bytes and verifies numerical/Unicode differences; tests do not derive inputs
from expected byte strings.
