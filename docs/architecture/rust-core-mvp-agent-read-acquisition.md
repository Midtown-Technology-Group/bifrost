# C1-R committed-read acquisition

2026-10-02. Root accepts the independently reviewed amendment for bounded
two-file SOURCE implementation only. No runtime/nominal/oracle release.
Independent candidate review fbe0b69eb73e149892d7cf1dda3aca9753b72e623e835b72ef47d88c2b4380d1
accepted revised7803c778 with the conditions explicitly incorporated below. Reference
HEAD a01437e68948ec08d96c070229e93144f40cde43. Current reader source
f3258a0346f7d6c01f01313780b2742afccb4cd77c828dfb85fb32637b12f021;
units 0ea55bca6d48ae440d5796fca97095a0918ceb6ae61902edeb87b9d518ba793c.
Read source was docs HEAD c5559c33f3168c8f5aa1db7220db45a0ffe74f73. Final reader
packet 9eb08293dfd5ee408d310bd7e21acb8f618cdaab4cece2861d4ce0852f6c83ac
remain governing except the explicit supplement selected after review here.
Guidance engineering-flow2026-09-30.1/design2026-10-01.5. No #1017 repair.

## Problem and limits

Isolation/read-only flags, later caller timestamps and a new Python wrapper
cannot identify a new committed-state read after actual closure. A transaction
identifier alone cannot establish its order relative to closure either. Preserve
both source-generated acquisition metadata and the actual trusted consumer's
same-process closure/awaited-call observation. These are instrumentation of the
isolated test observer, not production runtime credentials or authentication.
No new schema, permanent XID allocation, writer, reconnect, pool ownership,
row/advisory lock or public API is introduced. A malicious Docker-admin test
coordinator remains outside the existing trust model; these observations expose
incorrect acquisition/recycling, not cryptographic attestation against that host.

## Reader-owned types

All new dataclasses are frozen, slots and repr=False; nested values immutable.

- `TransactionIdentity(postmaster_started_at: datetime, backend_pid: int,
  virtual_xid: str)`: actual backend/server metadata; aware timestamp; exact
  positive native int PID <=2147483647, never bool; canonical ASCII virtual ID
  matching `[1-9][0-9]{0,9}/[1-9][0-9]{0,9}`, each component <=4294967295.
  This selected instrumentation rejects unexpected formats rather than adapting.
  The string is at most21 ASCII bytes. PID is not the virtual-backend-ID component.
- Extend `TransactionFacts` with `identity: TransactionIdentity`. Isolation and
  `read_only` still come from actual settings SELECT, never requested arguments.
- `ReadAcquisition(started_ns: int, completed_ns: int,
  transaction: TransactionIdentity)`: strict positive native int monotonic
  samples taken inside the reader. `started_ns` is sampled immediately before
  the actual bounded BEGIN await, after rejecting an active caller transaction;
  `completed_ns` only after actual COMMIT, successful False transaction-state
  readback and remaining-budget check. Require start<=completion<=unchanged
  operation/case end. An acquisition is no proof of domain eligibility/closure.
- `ReadResult.acquisition: ReadAcquisition | None`: only observed/changed after
  successful transaction exit carry it; failed/tainted/cancelled carry no usable
  snapshot/acquisition. Existing API parameters and E discovery are unchanged.
  Setup/run snapshot facts and acquisition must contain the same actual identity.

Capture this fixed actual metadata immediately after BEGIN/settings and again
after the admitted identity rechecks but BEFORE COMMIT:

```sql
SELECT pg_catalog.pg_backend_pid() AS backend_pid,
       pg_catalog.pg_postmaster_start_time() AS postmaster_started_at,
       l.virtualtransaction AS virtual_transaction,
       l.virtualxid AS own_virtual_xid,
       l.mode AS own_lock_mode,
       l.granted AS own_lock_granted
FROM pg_catalog.pg_locks AS l
WHERE l.pid = pg_catalog.pg_backend_pid()
  AND l.locktype = 'virtualxid'
  AND l.mode = 'ExclusiveLock'
  AND l.granted IS TRUE
  AND l.virtualxid = l.virtualtransaction
LIMIT 2
```

Require exactly one row with EXACT six aliases and strict scalar types;
virtual_transaction==own_virtual_xid, mode exactly ExclusiveLock, granted is
exactly True. Before/after identities must be equal within this transaction.
Zero/malformed/native/format/inconsistent/changing within-call identity maps
to existing MATERIAL_INVALID; missing or extra aliases map to SCHEMA_MISMATCH;
multiple rows map to CARDINALITY_EXCESS. These are FAILED, never CHANGED,
and carry neither snapshot nor acquisition. Across calls this stateless reader
faithfully exposes even equal identities; it does not certify freshness or
retain/reject prior state. Rollback
only under the SAME remaining budget. Any uncertain control/timeout/cancellation
retains existing dedicated-connection termination and tainted rules. Do not infer
a completed server rollback or reuse after termination. No field values/errors
in diagnostics. Reserve4096 bytes from the unchanged1MiB snapshot budget once
before these two bounded metadata fetches; never exceed caps or open an extra
timeout. Source-defined format/native scalars and LIMIT2 bound returned metadata;
Each returned six-alias metadata row is conservatively charged1024
(256 row +6*128 scalar aliases). The normal two one-row reads consume2048;
a multiple-row negative after the first valid query consumes at most3072
before immediate failure. Remaining1024 covers the fixed identity/acquisition
scalars retained on successful exit. The reserve is conservative bookkeeping,
not a backend lock-manager memory bound.

PostgreSQL16 holds its own exclusive virtual-ID lock for each transaction.
The identity may change between independent transactions; backend PID may also
change under transaction pooling, and must NOT be required equal across reads.
Its stability WITHIN a bounded transaction is required. Server epoch/backend
PID/virtual ID is neither universally unique forever nor evidence of when a
transaction began. No reconnect/server restart/virtual-counter-wrap recovery is
permitted to manufacture success; aliasing fails safe if the candidate cannot
distinguish acquisitions. The selected supported lane uses its actual verified
PostgreSQL16 service, not an invented production-pool deployment claim.
`pg_locks` collects internal lock-manager information and incurs synchronization;
this is not lock-free, globally coherent MVCC or per-row-lock proof. Cost must
be measured in the supported reader validation and reduced polling if excessive
without weakening final read provenance.

Primary references: [PostgreSQL16 pg_locks](https://www.postgresql.org/docs/16/view-pg-locks.html),
[system functions](https://www.postgresql.org/docs/16/functions-info.html),
[virtual transaction IDs](https://www.postgresql.org/docs/16/transaction-id.html).

## Separate consumer/oracle requirement — NOT released here

The trusted live consumer must capture actual observer-closed receipt/required
host-disposition joins, plus the same-process monotonic receipt time, BEFORE
calling/awaiting a NEW final reader invocation. Its frozen input must bind
lane/case/actual RunIds, unchanged deadline, actual closed evidence and the exact
returned ReadResult/acquisition. Retain the prior preclose transaction identity
and acquisition; require postclose final identity distinct, started_ns >= actual
closure receipt time, completed_ns >= started_ns, actual caller invocation
bracketing the reader samples, and no later clock/deadline substitution.
Require the same verified server incarnation/owned DB installation; do not
compare DB wallclock timestamps with host monotonic values. No snapshot refresh
by wrapping/relabeling it; no synthetic fresh/closed flag, UUID nonce or ordinal
alone. The exact live consumer dataclasses/call site/closure provenance still
need their separate freeze before oracle postclose can be complete. Missing
evidence remains pending/failed, NEVER nominal acceptance.

## JSON depth clarification

The selected private DB material bound is64 nested JSON CONTAINERS: root
container counts1; containers at recursive depths0..63 are allowed and scalar
leaves at depth64 are allowed. Root scalar is depth0. A65th container fails even
when empty. Add exact64/65 empty and mixed object/array boundary tests; do not
change existing wire/event decoder conventions or public payload caps. Finite
numbers, duplicate keys, invalid Unicode and actual source shapes remain strict.

## Future bounded source package and acceptance

Root releases C1-R-MATERIAL-ACQ-S: amend ONLY the two existing
untracked reader/unit files. Preserve all fixed domain queries/cardinality,
full native alias charges, Solution membership and readonly/timeout semantics.
Units must expose repeated/equal transaction identity without inferring
freshness; consumer/oracle must separately reject recycling. Detect changes within
one call, invalid types/format/cardinality, zero rows, no new permanent-XID or
locking query, metadata count/budget, missing acquisition, BEGIN/COMMIT stalls,
deadline and cancellation/uncertainexit, plus container-depth boundaries.
Pure synthetic units do NOT establish installed PG provenance or actual closure.
Later supported real validation must prove two read-only transactions produce
distinct identities and stable per-transaction before/after metadata; actual
committed-state/pre/postclosure negative tests remain consumer acceptance gates.
Required source review, locked Ruff0.15.12, supported scoped units/quality,
literal clean committed pre-pr and actual reference are separate gates.
Builder may write only the two source files and run stdlib AST/exact
Ruff0.15.12 source checks. No own-module/dependency imports, product execution,
pytest, Docker, PG, runtime, CI, commit or push; root owns publication and
supported verification after independent implementation review. Stop on source/version/permission/format
contradiction, broad custom observer machinery or any authority/schema change.

Frozen root source-release packet SHA256:
`dab896c7f60af0b3cd0b16a28eb7577385fa74c608dab7d2c2afe2289e341378`.
The implementation supplement must receive a different source review before
supported verification. Pure-unit acquisition receipts do not certify actual
PostgreSQL transactions or a live closed observer.
