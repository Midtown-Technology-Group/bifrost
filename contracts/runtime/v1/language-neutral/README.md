# Language-neutral document foundation

Proposed static package under bifrost.runtime/v1; not a negotiated wire profile.
The existing unmerged control_profile/v1 codec and its Hello remain unchanged.
See [contract](../../../../docs/architecture/runtime/contract.md) and
[review](../../../../docs/architecture/runtime/review.md).

| File | Contract |
| --- | --- |
| artifact.schema.json | Closed interpreted/native/managed immutable artifact evidence |
| workload.schema.json | Input, success/error Result, bounded log batches and usage |
| provision-binding.schema.json | Non-secret grant binding metadata; no credential authority |
| compatibility.schema.json | Existing workspace workflow/binding/ordered HTTP/result/error envelope |
| structural-vectors.json | Positive/negative document validation examples |
| required-scenarios.json | Coverage roster, not runnable scenarios or passed tests |
| fixtures/ | Six unchanged credential-free workspace cases and exact provenance |
| check.py | Offline schema/vector check |

Timestamps use calendar-valid UTC with a Z suffix and at most six fractional
digits. The offline checker enforces this without optional format packages.

All structural object fields are required and extra fields fail. Arbitrary
business input/output/error details remain opaque JSON governed by accepted
workload schemas. JSON Schema cannot detect duplicate keys, lexical 1.0/-0,
UTF8 framing or actual durable authorization. This check consumes synthetic
parsed documents only. Digests/UUIDs are structural evidence, not trust.

Run the offline check with the repository's locked jsonschema==4.26.0 available:

```sh
python3 contracts/runtime/v1/language-neutral/check.py
```

No product stack is necessary for static document checks. Production admission
and any runtime integration require the repository's supported VM/CI lane.
The behavioral schema preserves the existing workspace case format, including
ordered HTTP exchanges, secret references and exact result/error observations.
Header names compare case-insensitively; query order is irrelevant, request array
order is significant; empty http requires no network. A fresh sentinel must be
absent from output, errors, logs, history and serialized state.
The package still needs an executable HTTP/security oracle and runtime runners. Each concrete
case must contain its actual inputs, expected output/error and observed-effects
expectations; the required roster must never be passed off as executed vectors.
