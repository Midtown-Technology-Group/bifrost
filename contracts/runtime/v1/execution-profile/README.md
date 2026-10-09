# execution_profile/v1 proposal package

**Not frozen. Not production implementation.** Normative proposal:
[execution-profile.md](../../../../docs/architecture/runtime/execution-profile.md).
The proposed profile uses `bifrost.runtime/v1` framing with explicit Offer/Select
negotiation. It never alters or widens the unmerged `control_profile/v1` codec.

| File/layer | What a passing check means | What it does not mean |
| --- | --- | --- |
| profile.schema.json / structural-vectors.json | decoded closed message shapes, references and bounded fields | lexical/stream codec, session validity or authority |
| wire-vectors.json | exact raw length-prefixed specimens match the test-only parser oracle | real Rust/Python codec interchange, partial IO or real transport |
| session-vectors.json / oracle.py | synthetic direction/frontier/custody/owner assumptions yield specified decisions | actual committed Start, authorization, process permission or durable acceptance |
| upstream-boundary.json / boundary.py | pinned Python source inventory has not changed | baseline Python is isolated/safe, dynamic imports/packages or process conformance |
| references.json | identifies P0/architecture/Go review sources and P0 hashes | promotion of unmerged source, execution or preservation proof from an absent codec |

Wire vectors carry `hex` bytes and exact `error` or null for acceptance. Empty hex
with null is clean EOF, not a valid message. Implementations must also test real
stream partial IO; a specimen checker cannot prove it. The max-frame recipe, when
present, expands `prefix_hex + repeated byte * count + suffix_hex` literally.
Structural vectors carry decoded `frame` and mathematical-shape expectations;
wire control integers additionally reject floats/bools/lexical negative zero.

Session corpus has one `environment` (parent-assumed binding/accepted descriptor),
named `fixtures` of explicit steps, and independent `cases`. To run a case, start
fresh, append its named setup fixture (or none) and then its steps. Every step has
`input`, expected static `error`/null and exact `after` snapshot. No Python code is
needed to expand fixtures. A rejection changes neither sequences nor any other
state. `receive` steps use a decoded frame serialized compactly in listed key order,
or optional literal `hex`; retain its raw payload bytes for Result digest.

Trusted synthetic events are `commit_start`, `admit_grant`, `deliver`,
`observe_start`, `release`, `tick`, `cancel_commit`, `failure_commit`, `observe_cancel`, `revoke`,
`accept_result`, `close`, `transport_loss`, `child_crash`, `adapter_crash` and
`cleanup_verified`. They model independently verified parent/custody inputs, not
runtime-provided claims. `accept_result` assumes business output schema and current
owner/fence validation; `release` assumes actual descriptor/input/context/fresh
eligibility checks. `cleanup_verified` assumes OS inventory/wait evidence. Their
booleans cannot be used as a production proof. Oracle winner `failure`/`cancel`
is not a new public domain status; actual failure mapping remains reviewed.

Independent implementation rule: use schemas, normative prose and raw vectors as
inputs, not the oracle's internal Python state fields. Keep codecs, session tests,
actual process/authorization and durable-owner acceptance reports separate.

Offline checks (repository jsonschema dependency; no platform stack):

```sh
python contracts/runtime/v1/language-neutral/check.py
python contracts/runtime/v1/language-neutral/test_check.py
python contracts/runtime/v1/execution-profile/check.py
python contracts/runtime/v1/execution-profile/test_boundary.py
ruff check contracts/runtime/v1/execution-profile/*.py
```

The existing CI lint lane runs these alongside the retained foundation. Required
pre-PR/CI/review gates remain in force. No passing count certifies Rust-owned
execution, credentials, process topology, durable receipts or a profile freeze.
