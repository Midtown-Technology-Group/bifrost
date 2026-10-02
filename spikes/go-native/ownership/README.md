# Mechanical writer exclusion candidate

This disposable PostgreSQL experiment advances a shared Rust prerequisite. It
does not register a Go workflow, add a production migration, store a BiFrost
result, or satisfy the full C2/C3 gate. Its `agent_runs` and `execution_attempts`
fixtures use a deliberately small subset of existing fields at platform
`e58db4955ddd30177bd613f1d85b7e203ad7832a` plus a proposed immutable
`control_owner`. Both Python and Rust ownership are exercised; language is not
part of the database policy.

The database owner and security-definer guard owner are separate NOLOGIN roles.
The three runtime logins are non-owner, non-superuser and non-BYPASSRLS. Guards
use authenticated `session_user`, never a caller-controlled setting or the
function's `current_user`. Ownership and attempt identity cannot be reassigned.
Ancillary summary writes have an exact column allowlist; lifecycle mutations,
usage metering and reviewer verdicts receive no blanket summary exemption.
Unknown future columns are protected by the whole-row comparison.

CI connects separately as each actual runtime login and exercises positive
owner writes, cross-owner finalization/deletion, attempt mutation, ownership
forgery, summary escalation, SET ROLE, trigger disablement, replication-role
bypass, TRUNCATE and a forged custom setting. Final readback checks committed
fixture data using the incumbent reader. This is database evidence, not an
Execution API acceptance test.

Run only in hosted CI or an authorized disposable test VM. The hosted PostgreSQL
service uses local trust authentication solely for synthetic disposable roles;
there are no application credentials. Do not apply fixture SQL to a BiFrost DB.

Remaining acceptance work: reviewed additive Alembic schema and all actual
tables/FKs, unchanged Python public behavior, actual hidden writer and summary
metering paths, concrete annotation/delete authority, source-accounting and
credential lock order, concurrent race tests, PgBouncer distinct backend login
readback, mixed ownership rollback and drain, and full shared protocol/session
authority. A shared backend login followed by SET ROLE fails this design's
identity requirement. It must never be silently treated as separate custody.
