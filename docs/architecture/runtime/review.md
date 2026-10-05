# Language-neutral runtime foundation review

Status: proposed contract package; no runtime release or authority transfer.
Review baseline (2026-10-05): MTG platform main `e2cbbb3702`; C1-P0
`9f35278f90ba3757318ac9f07895bda9957330de` (#1015); architecture amendment
`93ec9421b23b0b4a1493ab45f15f55ded3377d2e` (#1011); Go spike
`1783de8a3154a07d434acf76111fc53f7154a4e1`. These branches remain separate
from main. The spike supplies evidence, not the implementation baseline.

The supplied proposal preserves the right authority boundary. Rust owns
admission, committed Start, cancellation, process custody, durable result
acceptance and replay policy. SDKs consume narrow bound capabilities; tenant
code never gains lifecycle authority. Go initialization requires a trusted
adapter outside tenant code, rather than a language exception.

## Gaps and decisions

| Gap | Foundation decision | Remaining proof |
| --- | --- | --- |
| P0 requires interpreter-shaped evidence and denies unknown fields | Keep P0 unchanged. Publish separate language-neutral document schemas. Do not send them through P0 Hello. | Reviewed full-profile negotiation and real Python/Rust codec interchange |
| Hash identity is self-referential/undefined | artifact_id identifies the accepted immutable deployment bundle bytes; descriptor is external evidence, not its own hash preimage. Separate payload/build/adapter hashes bind actual bytes. | Accepted deployment owner verifies each digest and closes all mutable runtime inputs |
| Native artifact can omit adapter identity | Require adapter digest for every class; admission validates it independently. | Actual executable/image custody and descendant cleanup |
| Scope and null semantics differ across languages | Explicit required fields, including nullable organization/deadline/image. Null organization denotes explicitly admitted global scope; it never grants global access. | Original-caller/effective-scope and session closure enforcement |
| JSON Schema does not specify lexical JSON semantics | Schemas validate decoded documents only. Full codec must retain P0 UTF8, duplicate-key, depth and lexical-number requirements. | Unchanged incumbent vectors plus new cross-language negatives |
| Result races can masquerade as success | Child Result is an observation; parent durable acceptance serializes against cancellation/fences. No Result reopens a cancelled session. | Real receipt/projection transactions, concurrent race tests |
| SDK retries may duplicate effects | Read retries need explicit eligibility; mutating retries require owner-defined idempotency. Lost ACK is unknown, never automatic replay. | Actual transport/provider failure tests |
| Cancellation may leave children/effects | Adapter propagates cancellation and enforces bounded descendant custody; cancellation never asserts rollback. | OS custody, SDK waits, deadlines and partial effects |
| Fixture format alone can imply conformance | Separate structural vectors, required coverage roster and executable behavioral cases. No placeholder workflow counts as execution. | Same real workload vectors and ordered HTTP/security oracle against Python and Go |
| Freeze lacks evidence criteria | Freeze only after owner review and honest Python/Go reconciliation. Deno/.NET follow later. | Independent implementation and common observable semantics |

## Scope

This implementation supplies static schemas and executable offline structural
validation on current MTG main. It does not import the unmerged Rust platform,
change the P0 codec, wire credentials, install language runtimes, implement
workflow ownership, deploy, or redesign Ninja/Sopdet. Those are subsequent
slices beneath the existing #1011 gates. The original document's “freeze” is an
acceptance milestone, not a status achieved by writing these schemas.
