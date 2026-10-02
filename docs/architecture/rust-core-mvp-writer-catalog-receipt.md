# WEX-CAT0: isolated catalog diagnostic implementation package

Architect disposition: **released for bounded test-only implementation** after
independent source review. This release covers the four owned diagnostic/test/CI
paths below. It does not release Stage1 role, schema, guard or authority changes,
or C2/C3. No runtime evidence has yet been collected for this package.

Frozen reviewed design SHA256:
`a687ab1cc2d24509eb3341776d274fdd290fce0139476d6198113e4da9f71d43`.
Independent package review SHA256:
`20f06fd130d8ad8d8b2b963f41b8c604617e510cbdf6f0f70522131e315983b6`.
The source-design record below retains its original proposal status; this
architect disposition is the test-only implementation release. Subsequent
implementation must receive distinct source review and supported verification.
Actual installed graph, source reconciliation and security readiness remain
unproved. The receipt observes two test-runner sessions, not deployed API,
worker/scheduler principals or mechanical mixed-writer exclusion.


## Architect verification-lane amendment

The four-file source implementation received distinct independent source review:
`b5179ee1d5c274202b3f2effaee8b164c2abd15782408643ce7329fcff861011`.
This accepts source for supported verification; no unit, quality, catalog or
custody test has run for it yet.

Current main `4abdf1a163986b6bd86aa7bafe6fa56b963acbb6` has no hosted job
that invokes the required literal clean-candidate `./test.sh pre-pr`. Ordinary
CI cannot substitute for that gate. The architect releases this precise
verification-only exception to the original no-input/no-job workflow scope:
reuse the optional `pre_pr` boolean input and the existing Manual Pre-PR Gate
from reviewed source `659c85261c3260745c1b678fda12d40d102df26d`, without
other prior-branch files or the identity uploader. Four-path ownership remains
unchanged. This is an explicit package amendment, not compliance with the
original narrower workflow rule.

Independent amendment review SHA256:
`f5e54e656292f12d18e618dd709ae79e34d34d7b78b9b802c47d47f3d3b89208`.
Freeze the six inserted input lines at SHA256
`786869cc02add641f8b97ef997ce1e871b58b78b188ea0e532de47ee353bd342`
and the 130-line job section including its two leading comments at SHA256
`b4aad74c032f4a238d445abf0709b0ebb9535a691f92d76a37142e4ef119f73c`.
Exact inverse removal must recover the accepted candidate workflow. Obtain
independent insertion review before dispatch.

The optional job runs only for this repository's explicitly selected manual
branch event, checks out `github.sha`, invokes the unchanged gate and preserves
its clean-candidate/current-main checks. Existing required jobs, permissions,
action pins and diagnostic uploader remain unchanged. The optional job has
contents/packages read permissions and retains the prior reviewed isolated
pre-PR logs/JUnit as well as candidate, stage, image and actual project-resource
cleanup evidence; this capture is broader than the original receipt-only
uploader and is explicitly included in this exception. It releases no product,
role, DDL, schema, runtime, deployment or lifecycle authority.

Image preparation may use the existing reviewed-source rebuild path. Record
actual image ID/digest or local-build custody rather than assuming registry
consumption. A passing pre-PR job may defer E2E and does not establish catalog
collection. Actual named nonskipped catalog JUnit, receipt readback hash and
candidate/tree/run/job/shard/image/artifact association remain mandatory.
Cleanup evidence applies only to its own job. Stage1 and C2/C3 remain stopped.

## Architect test-seam and supported-directory clarifications

The pure helper retains matrix/graph/serialization responsibility and no
connection, environment or product imports. Unit tests may import the test-only
E2E module as `collector` to patch its asyncpg/OS seams. Import must perform no
connection, query, task creation or receipt write. Do not import its actual
`test_collect...` function into unit globals or inherit E2E marks/fixtures.
Use a function-scoped deny-real-connect sentinel; explicit fake overrides are
required for connection tests. FD red cases use only owned temporary paths
through unit patching of the fixed receipt constant. The real diagnostic gets
no configurable output or target fallback.

`test.sh:364–370` deliberately chmods the host-owned results mount to777 for the
uid1000 test container. Parent directory ownership by the container and private
parent mode are not collection admissions. Open fixed `/bifrost-results` with
O_DIRECTORY/O_NOFOLLOW/CLOEXEC, retain its FD, create the fixed receipt basename
exclusively relative to that FD, and verify a new euid-owned0644 regular file
with one link. Verify parent/path/device/inode/owner identity before and after
readback; record supported parent facts as metadata. Do not chmod existing
files/directories or overwrite a collision. All awaits and post-blocking-I/O
success checks remain inside the absolute budget; kernel I/O preemption is not
promised. This detects bounded custody failures, not immutable namespace or
privileged-host authority. The world-writable supported mount must never be
represented as a secure secret/capability transport or reused as Stage1 proof.

These clarifications retain the four owned paths and frozen SQL/native schemas;
they authorize only the diagnostic test seams, not a generic effectful helper,
new harness fixtures, host implementation, permission changes or runtime use.

# Proposed isolated writer catalog receipt package — source design only

Authority: accepted FK inventory `/tmp/bifrost-writer-fk-lock-source-inventory.md` SHA256 `128bedf83b2609f100f6e241e91b8c27726e01b7a5f337d5cdb0cf422e3d16b5`. Platform API baseline main `4abdf1a163986b6bd86aa7bafe6fa56b963acbb6`; inspected docs worktree HEAD `913760e4ddbe75d86c1a9a3d18f3a589c74b1e34`, clean when inspected. This is proposal only, not an implementation or execution authorization.
Loaded engineering-flow `2026-09-30.1`, bounded evidence-design route; root AGENTS and durable-job/verification sections retained. No new job framework, principals, migration history, schema, issuer, role/DDL experiment or Stage1/C2/C3 acceptance.

## Candidate ownership and reuse decision

Use a standalone narrow candidate from the accepted current platform baseline; do not inherit the 45-file reference implementation. Current main does not contain the identity collector or its successful-evidence upload step. Source precedent only: `/home/thomas/src/bifrost-agent-capacity-reference/api/tests/e2e/platform/test_writer_identity_preflight.py` SHA256 `fe157094fd0de96ec18e8267df22d46e7cce7773f84c81453dfc35943072e0b0`. Reuse its closed endpoint checks, repeatable-read/read-only transactions, native-type validation, safe exception boundary, tabular receipt, exclusive file/readback design by explicit reviewed extraction; do not import a test module or products into a generic collector.
Proposed owned files, each with one purpose:
- `api/tests/e2e/platform/test_writer_catalog_receipt.py`: one combined direct+pool diagnostic, fixed SQL statements, endpoint/deadline/transaction orchestration, receipt file custody. Keep code readable and apply only the actual current repository file-size rules; no inherited arbitrary250-line cap.
- `api/tests/helpers/writer_catalog_receipt.py`: pure bounded schema/type/cross-query validation and serialization; no connection/env/factory/product imports, no generic query engine. This extraction supports independently red-capable unit tests, rather than hiding code to meet an invented line cap.
- `api/tests/unit/test_writer_catalog_receipt_contract.py`: malformed/missing/capped/changed-graph/type/error/custody signals against that pure contract. SQL constants remain in the E2E file: no configurable query text or SQL-file parser.
- `.github/workflows/ci.yml`: **associated verification scope requiring root architecture review within the already authorized test-only goal**, one fixed always-upload step in the existing `test-e2e` job; no other workflow/target/gate changes. If capture is not reviewed/supported, stop before claiming hosted receipt delivery; no new user-permission request is required for this uploader.

## Relevant closure, not undirected component

Exact 12 public roots (fixed literals, no caller-provided relation lists): executions; workflow_execution_attempts; agent_runs; agent_run_steps; execution_attempts; execution_lifecycle_events; work_deliveries; execution_logs; ai_usage; agent_run_verdict_history; agent_run_flag_conversations; poison_message_dispositions.
A = roots plus recursively referenced ancestors, following child→parent FK. D = roots plus recursively referencing dependents, following parent→child FK. V = A ∪ D. Return every FK incident to V, and its other endpoint as an explicitly marked one-hop boundary B. Do **not** recursively expand B, or switch direction at every ancestor: that becomes an undirected component and pulls unrelated retained PlatformJobs/devices through user/org identity. Paths that return from an ancestor branch into a protected root are already in A; immediate side-branch FK barriers/mutations are visible in incident edges. Deeper collateral side-branch trees remain explicitly outside this relevant receipt; no claim of full database component coverage.
Root's source counts: 122 declared tables, 191 literal simple FKs; undirected root component already at least 107 relations, before composite/aliased/migration-only edges. The same simple-FK source model gives189 distinct child/parent relation pairs, A including roots28 relations, D including roots13, V28, incident edges150 distinct relation pairs, boundary67, S95. These are deduplicated relation-pair counts, not installed constraint counts, and omit composites/aliases/migration-only edges. Even directional+one-hop catalog scope covers much of declared schema; every classification remains metadata-only. Thus 64 relations is unsupported. New limits below intentionally permit broader **catalog metadata** discovery, never permission/ownership expansion. Additional relations are observations pending source reconciliation; they do not acquire proposed Rust ownership.
Cycles terminate because recursive terms store only OID and use UNION (deduplication), not paths/depth/UNION ALL. A/D classification is retained, including self-FKs. LIMIT bounds returned rows, not recursive evaluation; statement and overall deadlines bound work. Receipt is not a direct-delete eligibility calculation.

## Fixed catalog SQL

Each statement is executed inside one repeatable-read, read-only transaction per endpoint. No SET ROLE, DDL/DML, arbitrary SQL/targets or product table reads except `public.alembic_version.version_num`. Cast internal catalog char codes explicitly to text; record that source representation, preserve observed values, never normalize permissions/actions. Each row set is sorted and SQL-limited to cap+1; cap+1 is failure, not truncation acceptance.
Q0 roots (cap 12); exactly one ordinary public table per fixed name required:
```sql
WITH roots(name) AS (VALUES
 ('executions'),('workflow_execution_attempts'),('agent_runs'),('agent_run_steps'),
 ('execution_attempts'),('execution_lifecycle_events'),('work_deliveries'),
 ('execution_logs'),('ai_usage'),('agent_run_verdict_history'),
 ('agent_run_flag_conversations'),('poison_message_dispositions'))
SELECT r.name,n.nspname,c.oid::bigint AS relation_oid,c.relkind::text
FROM roots r LEFT JOIN pg_catalog.pg_namespace n ON n.nspname='public'
LEFT JOIN pg_catalog.pg_class c ON c.relnamespace=n.oid AND c.relname=r.name
ORDER BY r.name LIMIT 13;
```
Q1 identity (cap 1):
```sql
SELECT current_database() AS database_name,session_user::text,current_user::text,
 pg_catalog.pg_backend_pid() AS backend_pid,
 pg_catalog.pg_postmaster_start_time() AS postmaster_started_at,
 current_setting('server_version_num')::integer AS server_version_num,
 current_setting('transaction_read_only') AS transaction_read_only,
 current_setting('transaction_isolation') AS transaction_isolation LIMIT 2;
```
Q2 role identity/authority (cap 256):
```sql
SELECT r.oid::bigint AS role_oid,r.rolname,r.rolsuper,r.rolinherit,
 r.rolcreaterole,r.rolcreatedb,r.rolcanlogin,r.rolreplication,r.rolbypassrls,
 pg_catalog.pg_has_role(session_user,r.oid,'MEMBER') AS session_member,
 pg_catalog.pg_has_role(session_user,r.oid,'USAGE') AS session_usage,
 pg_catalog.pg_has_role(current_user,r.oid,'MEMBER') AS current_member,
 pg_catalog.pg_has_role(current_user,r.oid,'USAGE') AS current_usage
FROM pg_catalog.pg_roles r WHERE r.rolname IN (session_user,current_user)
 OR pg_catalog.pg_has_role(session_user,r.oid,'MEMBER')
 OR pg_catalog.pg_has_role(current_user,r.oid,'MEMBER')
ORDER BY r.oid LIMIT 257;
```
Fixed prefix P for Q3–Q7, copied from one constant, never user interpolation:
```sql
WITH RECURSIVE roots(oid) AS (
 SELECT c.oid FROM pg_catalog.pg_class c
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relname IN (
 'executions','workflow_execution_attempts','agent_runs','agent_run_steps',
 'execution_attempts','execution_lifecycle_events','work_deliveries',
 'execution_logs','ai_usage','agent_run_verdict_history',
 'agent_run_flag_conversations','poison_message_dispositions')),
 fk AS (SELECT * FROM pg_catalog.pg_constraint WHERE contype='f'),
 a(oid) AS (SELECT oid FROM roots UNION
 SELECT k.confrelid FROM fk k JOIN a ON k.conrelid=a.oid),
 d(oid) AS (SELECT oid FROM roots UNION
 SELECT k.conrelid FROM fk k JOIN d ON k.confrelid=d.oid),
 v(oid) AS (SELECT oid FROM a UNION SELECT oid FROM d),
 e AS (SELECT k.* FROM fk k WHERE k.conrelid IN (SELECT oid FROM v)
 OR k.confrelid IN (SELECT oid FROM v)),
 s(oid) AS (SELECT oid FROM v UNION SELECT conrelid FROM e
 UNION SELECT confrelid FROM e)
```
Q3 relations after P (cap 256):
```sql
SELECT c.oid::bigint AS relation_oid,n.nspname,c.relname,c.relkind::text,
 c.relowner::bigint AS owner_oid,pg_catalog.pg_get_userbyid(c.relowner) AS owner,
 n.nspowner::bigint AS schema_owner_oid,
 pg_catalog.pg_get_userbyid(n.nspowner) AS schema_owner,
 c.relrowsecurity,c.relforcerowsecurity,c.relispartition,
 EXISTS (SELECT 1 FROM pg_catalog.pg_inherits h
 WHERE h.inhrelid=c.oid OR h.inhparent=c.oid) AS has_inheritance,
 c.oid IN (SELECT oid FROM roots) AS is_root,
 c.oid IN (SELECT oid FROM a) AS is_ancestor,
 c.oid IN (SELECT oid FROM d) AS is_dependent,
 c.oid NOT IN (SELECT oid FROM v) AS is_boundary,
 pg_catalog.has_schema_privilege(current_user,n.oid,'USAGE') AS schema_usage,
 pg_catalog.has_schema_privilege(current_user,n.oid,'CREATE') AS schema_create,
 pg_catalog.has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
 pg_catalog.has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,
 pg_catalog.has_table_privilege(current_user,c.oid,'UPDATE') AS can_update,
 pg_catalog.has_table_privilege(current_user,c.oid,'DELETE') AS can_delete,
 pg_catalog.has_table_privilege(current_user,c.oid,'TRUNCATE') AS can_truncate,
 pg_catalog.has_table_privilege(current_user,c.oid,'REFERENCES') AS can_reference,
 pg_catalog.has_table_privilege(current_user,c.oid,'TRIGGER') AS can_trigger
FROM s JOIN pg_catalog.pg_class c ON c.oid=s.oid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace ORDER BY c.oid LIMIT 257;
```
Q4 incident FK edges after P (cap 1024):
```sql
SELECT k.oid::bigint AS constraint_oid,k.conname,
 k.conrelid::bigint AS child_oid,k.confrelid::bigint AS parent_oid,
 k.conkey,k.confkey,
 ARRAY(SELECT z.attname FROM unnest(k.conkey) WITH ORDINALITY x(num,ord)
 JOIN pg_catalog.pg_attribute z ON z.attrelid=k.conrelid AND z.attnum=x.num
 ORDER BY x.ord) AS child_columns,
 ARRAY(SELECT z.attname FROM unnest(k.confkey) WITH ORDINALITY x(num,ord)
 JOIN pg_catalog.pg_attribute z ON z.attrelid=k.confrelid AND z.attnum=x.num
 ORDER BY x.ord) AS parent_columns,
 k.confdeltype::text,k.confupdtype::text,k.confmatchtype::text,
 k.condeferrable,k.condeferred,k.convalidated,k.conislocal,k.coninhcount,
 k.conparentid::bigint AS parent_constraint_oid
FROM e k ORDER BY k.oid LIMIT 1025;
```
Q5 PK/unique/check metadata after P (cap 2048):
```sql
SELECT k.oid::bigint AS constraint_oid,k.conrelid::bigint AS relation_oid,
 k.conname,k.contype::text,k.conkey,
 ARRAY(SELECT z.attname FROM unnest(k.conkey) WITH ORDINALITY x(num,ord)
 JOIN pg_catalog.pg_attribute z ON z.attrelid=k.conrelid AND z.attnum=x.num
 ORDER BY x.ord) AS columns,k.conindid::bigint AS index_oid,
 k.condeferrable,k.condeferred,k.convalidated,k.conislocal,k.coninhcount,
 k.connoinherit,k.conparentid::bigint AS parent_constraint_oid,
 CASE WHEN k.conindid=0 THEN NULL ELSE i.indisvalid END AS index_valid,
 CASE WHEN k.conindid=0 THEN NULL ELSE i.indnullsnotdistinct END AS nulls_not_distinct
FROM pg_catalog.pg_constraint k LEFT JOIN pg_catalog.pg_index i ON i.indexrelid=k.conindid
WHERE k.conrelid IN (SELECT oid FROM s) AND k.contype IN ('p','u','c')
ORDER BY k.oid LIMIT 2049;
```
Q6 internal and external trigger/function authority after P (cap 4096):
```sql
SELECT t.oid::bigint AS trigger_oid,t.tgrelid::bigint AS relation_oid,t.tgname,
 t.tgenabled::text,t.tgisinternal,t.tgtype,t.tgattr::smallint[] AS trigger_columns,
 t.tgconstraint::bigint AS constraint_oid,t.tgconstrrelid::bigint AS related_oid,
 t.tgparentid::bigint AS parent_trigger_oid,t.tgdeferrable,t.tginitdeferred,t.tgnargs,
 t.tgnargs>0 AS has_trigger_arguments,t.tgqual IS NOT NULL AS has_trigger_condition,
 ck.contype::text AS trigger_constraint_type,
 ck.conrelid::bigint AS constraint_relation_oid,
 ck.confrelid::bigint AS referenced_relation_oid,
 CASE WHEN ck.contype='f' AND (ck.conrelid IN (SELECT oid FROM v)
 OR ck.confrelid IN (SELECT oid FROM v)) THEN 'incident_fk'
 WHEN t.tgconstraint<>0 OR t.tgconstrrelid<>0 THEN 'outside_incident_scope'
 ELSE 'unbound' END AS reference_scope,
 p.oid::bigint AS function_oid,n.nspname AS function_schema,p.proname,
 n.nspowner::bigint AS function_schema_owner_oid,
 pg_catalog.pg_get_userbyid(n.nspowner) AS function_schema_owner,
 p.proowner::bigint AS function_owner_oid,
 pg_catalog.pg_get_userbyid(p.proowner) AS function_owner,
 o.rolsuper AS function_owner_super,o.rolbypassrls AS function_owner_bypassrls,
 p.prosecdef,p.proleakproof,p.provolatile::text,p.proparallel::text,
 p.proargtypes::oid[] AS argument_type_oids,p.prorettype::bigint AS return_type_oid,
 p.proconfig IS NOT NULL AS has_function_configuration,
 EXISTS (SELECT 1 FROM unnest(p.proconfig) z(setting)
 WHERE split_part(z.setting,'=',1)='search_path') AS has_configured_search_path,
 pg_catalog.has_function_privilege(current_user,p.oid,'EXECUTE') AS can_execute
FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
JOIN pg_catalog.pg_roles o ON o.oid=p.proowner
LEFT JOIN pg_catalog.pg_constraint ck ON ck.oid=t.tgconstraint
WHERE t.tgrelid IN (SELECT oid FROM s) ORDER BY t.oid LIMIT 4097;
```
Q7 policies after P (cap 1024):
```sql
SELECT p.oid::bigint AS policy_oid,p.polrelid::bigint AS relation_oid,p.polname,
 p.polcmd::text,p.polpermissive,p.polroles::oid[] AS role_oids,
 p.polqual IS NOT NULL AS has_using_expression,
 p.polwithcheck IS NOT NULL AS has_check_expression
FROM pg_catalog.pg_policy p WHERE p.polrelid IN (SELECT oid FROM s)
ORDER BY p.oid LIMIT 1025;
```
Q8 migration revision (cap 16; require exactly the sole source-DAG head `20261001_solution_src_account`):
```sql
SELECT version_num FROM public.alembic_version ORDER BY version_num LIMIT 17;
```
The main4ab source DAG independently has317 unique declared revisions, no missing parents, sole head `20261001_solution_src_account` (`20261001_solution_owned_source_accountability.py`). Missing/multiple/unexpected installed heads fail with bounded safe revision metadata; never stamp/repair.
No check/policy/trigger expressions, definitions, expression hashes or expression lengths are queried/exported. No trigger args, tgqual text, prosrc, proconfig values or function bodies are exported; Q6 retains native tgnargs and closed presence booleans only. Function/schema owner identity and security flags are structural authority observations. Thus this receipt cannot detect changed expression semantics with unchanged metadata, prove the usage OR check, policy predicate, trigger WHEN/arguments/body or configured search-path authority. All semantic source reconciliation remains explicitly missing before Stage1; no source-admission framework or name-based semantics is introduced. Fixed SQL/query/source hashes are provenance only, not hashes of secret-bearing catalog expressions. Zero policies/absent triggers is an observation, not desired security.

## Frozen ordered native column/type/nullability matrix

Notation: every type means exact Python native type (no subclasses/coercion), `?` alone permits None. B=bool. S=nonempty printable str, ≤128chars and128UTF-8 bytes. O=int1..4294967295; Z=int0..4294967295. I=int0..2147483647. H=int0..32767. P=int1..2147483647. T=datetime with non-None UTC offset, serialized ISO8601. AK=list of exact int1..32767; AS=list of S; AO=list of O; AZ=list of Z. Every list ≤128; suffix + means1..128 and * means0..128; AK? permits None or0..128 elements only for Q5 CHECK. Text codes are exact str: RK={r,i,S,t,v,m,c,f,p,I}; FKAction={a,r,c,n,d}; FKMatch={f,p,s}; PUC={p,u,c}; CT={p,u,c,f,t,x}; Enabled={O,D,R,A}; Volatility={i,s,v}; Parallel={s,r,u}; PolicyCmd={*,r,a,w,d}; Scope={incident_fk,outside_incident_scope,unbound}. Q3 requires relkindr, relispartition/has_inheritance false for supported ordinary noninherited relations (nonpublic ordinary relation metadata allowed). Integer0 is never substituted for missing joined data.

Each line below freezes **SQL return order**; there are no unnamed/adaptive columns:
- Q0: name:S, nspname:S?, relation_oid:O?, relkind:RK?. Null LEFT JOIN fields are admitted for parsing but then rejected by exact-root admission.
- Q1: database_name:S, session_user:S, current_user:S, backend_pid:P, postmaster_started_at:T, server_version_num:P, transaction_read_only:{on,off}, transaction_isolation:{read uncommitted,read committed,repeatable read,serializable}. Admission then requires bifrost_test, PG16, on, repeatable read.
- Q2: role_oid:O, rolname:S, rolsuper:B, rolinherit:B, rolcreaterole:B, rolcreatedb:B, rolcanlogin:B, rolreplication:B, rolbypassrls:B, session_member:B, session_usage:B, current_member:B, current_usage:B.
- Q3: relation_oid:O, nspname:S, relname:S, relkind:RK, owner_oid:O, owner:S, schema_owner_oid:O, schema_owner:S, relrowsecurity:B, relforcerowsecurity:B, relispartition:B, has_inheritance:B, is_root:B, is_ancestor:B, is_dependent:B, is_boundary:B, schema_usage:B, schema_create:B, can_select:B, can_insert:B, can_update:B, can_delete:B, can_truncate:B, can_reference:B, can_trigger:B.
- Q4: constraint_oid:O, conname:S, child_oid:O, parent_oid:O, conkey:AK+, confkey:AK+, child_columns:AS+, parent_columns:AS+, confdeltype:FKAction, confupdtype:FKAction, confmatchtype:FKMatch, condeferrable:B, condeferred:B, convalidated:B, conislocal:B, coninhcount:I, parent_constraint_oid:Z. All four arrays have equal length; names preserve composite order. Inherited coninhcount>0/nonlocal or nonzero parent unsupported.
- Q5: constraint_oid:O, relation_oid:O, conname:S, contype:PUC, conkey:AK?, columns:AS*, index_oid:Z, condeferrable:B, condeferred:B, convalidated:B, conislocal:B, coninhcount:I, connoinherit:B, parent_constraint_oid:Z, index_valid:B?, nulls_not_distinct:B?. For p/u: conkey/columns nonempty equal-length, index_oid positive and both index booleans nonnull. For c: conkey mayNone/empty; columns empty iff conkeyNone/empty, otherwise lengths match; index_oid0 and both index booleansNone. Parent/inherited exceptions STOP as Q4. These are structure checks; check predicate is absent.
- Q6: trigger_oid:O, relation_oid:O, tgname:S, tgenabled:Enabled, tgisinternal:B, tgtype:H, trigger_columns:AK*, constraint_oid:Z, related_oid:Z, parent_trigger_oid:Z, tgdeferrable:B, tginitdeferred:B, tgnargs:H, has_trigger_arguments:B, has_trigger_condition:B, trigger_constraint_type:CT?, constraint_relation_oid:Z?, referenced_relation_oid:Z?, reference_scope:Scope, function_oid:O, function_schema:S, proname:S, function_schema_owner_oid:O, function_schema_owner:S, function_owner_oid:O, function_owner:S, function_owner_super:B, function_owner_bypassrls:B, prosecdef:B, proleakproof:B, provolatile:Volatility, proparallel:Parallel, argument_type_oids:AO*, return_type_oid:O, has_function_configuration:B, has_configured_search_path:B, can_execute:B. tgtype must be1..127 (PG16 trigger bitmask), has_trigger_arguments equals(tgnargs>0). Joined CT/relation fields allNone iff constraint_oid0; for f both relation fields positive. parent_trigger_oid must0. Empty trigger_columns/argument_type_oids legal. No tgargs/config/condition content admission.
- Q7: policy_oid:O, relation_oid:O, polname:S, polcmd:PolicyCmd, polpermissive:B, role_oids:AZ+, has_using_expression:B, has_check_expression:B. RoleOID0 is PUBLIC, not missing identity. No policy expression semantics is observed.
- Q8: version_num:S; exactly one row and value20261001_solution_src_account. Additional/missing/duplicate rows fail fixed revision admission, not stamp/repair.

SQL text and matrix must be compared during independent implementation review, with exact asyncpg.Record.keys order. Catalog char::text/OID vector-array casts are deliberate SQL representation, not state normalization. Actual PostgreSQL codecs are still unexecuted here; empty observed classes cannot establish their nonempty native types. No new query is added to synthesize evidence.

## Bounds, consistency, receipt and stops

- Collection admissions are **only**: fixed DSN/DB/PG16; exact12 public ordinary roots; sole revision `20261001_solution_src_account`; frozen native/schema/cross-query contracts; supported nonpartition/noninherited relation/constraint/trigger contract; row/array/byte/deadline/custody limits; consistency of the two samples. Missing/duplicate roots or unsupported codecs/codes/partition parents fail collection. Additional ordinary relations/namespaces/edges/actions, migration-vs-ORM differences, unsafe owners/permissions, empty classes or missing semantic proof do not fabricate collection failure. A complete bounded observation may PASS with `reconciliation_status:reconciliation_pending` and `security_readiness:not_established`; no owner-scope expansion.
- Caps above are new, not inherited from 12-root identity receipt: 256 relations permits roughly twice 122 declared tables; 1024 FK rows permits over five times 191 simple FKs for composite/migration/boundary edges; constraints2048 and triggers4096 permit PK/checks and both endpoints' internal FK triggers with margin. These are ceiling allowances, not source assertions about actual counts. Exact-cap accepted only if cap+1 absent; any sentinel => safe failed bounded collection; never increase/retry automatically.
- Frozen ordered matrix above is mandatory; no stringification of unknown native types or implicit alias inference. Q0 must contain exact12 roots and Q3 their identities; Q4 endpoints resolve Q3; recomputed OID-only A/D/incident/boundary classifications match SQL. FK columns are ordered positive/nonempty/equal-length with exact name-pair count. Q5/Q7 relation IDs and Q6 tgrelid resolve S.
- Q6 joined constraint fields are NULL **only** when constraint_oid=0; any positive constraint_oid requires ck to resolve. For typef, ck child/parent are positive: if either lies in V, `reference_scope:incident_fk` requires exact constraintOID and endpoint equality with Q4; trigger relation is one endpoint and related_oid is its opposite endpoint (self-FK remains self). A dropped Q4 edge cannot be relabeled outside scope. If neither lies in V, typef is `outside_incident_scope`; its endpoints/related OIDs may lie outside S and are retained without expansion. Nonf constraints p/u/c on S must match Q5 identity/type/relation; t/x and nonf references outside S remain bounded observed metadata with reconciliation_pending, not guessed function-name authority. Any otherwise nonzero constraint/related reference is outside_incident_scope; zero constraint/related IDs are `unbound`. Q6 tgrelid always resolves S. This does not collect B's complete FK graph. Parent constraint/trigger IDs must be0, relation inheritance/partition flags false and inherited-constraint counts0; otherwise unsupported-contract STOP.
- Full graph snapshots exactly two, sequential: direct postgres:5432 and pool pgbouncer:5432; each includes Q0–Q8, own identity/backend/postmaster/start/end/query hashes/columns/native rows. No third giant snapshot or digest-only pool substitution. Snapshot result and structural OID/action/role metadata must agree after excluding backend_pid/timing; mismatch fails with bounded difference identities. Agreement proves two observations, not stable pool caller identity across future transactions or deployment.
- Closed URL admission follows precedent: existing TEST_DATABASE_URL only, exact driver postgresql+asyncpg, host pgbouncer, port5432, database bifrost_test, username bifrost, present password, no URL query/extra target configuration. Derive direct solely by changing host to postgres and driver to postgresql; never print URL, environment or password. Use statement_cache_size0. Existing autouse setup/reset, health checks and clean isolated test boot remain supported harness effects; only diagnostic transactions/queries are read-only. Never label whole invocation read-only or target live deployment.
- One absolute60s monotonic deadline and one enclosing60s asyncio timeout, no retries/reset. Every async connect/query/transaction enter/success exit/rollback/close await is gated by positive remaining budget and bounded by min(5s, remaining), close min(2s, remaining). Cleanup never starts an unbounded await after expiry/cancellation: synchronously terminate the connection if expired, close times out/fails or rollback cannot finish in budget. A close/rollback failure is collection failure even after complete SQL. Driver termination is cleanup, not SQL/role mutation.
- Catch errors only to store fixed endpoint/query/stage/failure code, then leave the except block, perform bounded cleanup and raise a new static failure **outside caught exception context**, with no driver exception text/traceback/query values. Recheck monotonic deadline after every blocking file write/fsync/read/close before marking success. Synchronous kernel I/O cannot be preempted by asyncio; no promise of strict kernel-time preemption. If file IO finishes late, fail collection instead of publishing success.
- Per-snapshot encoded tabular JSON cap4MiB, combined receipt8MiB; cap enforced during incremental byte accounting, not after an unbounded serialization. Counts and per-field/array limits bound materialization separately. Receipt stores schema `bifrost.writer-catalog-receipt/v1`, fixed purpose, exact test/helper source-byte hashes from admitted local test-only paths, fixed query/contract hashes, `reference_source_main:4abdf1a163986b6bd86aa7bafe6fa56b963acbb6`, exact snapshot provenance, `reconciliation_status:reconciliation_pending`, `security_readiness:not_established` and `collection_status:raw_complete_pending_custody`. Final collection success requires the actual named testcase PASS after all IO/deadline/custody checks, not a success flag prewritten into the file. The container does not invent an actual candidate SHA/tree/run/image from environment or assume .git/GITHUB_SHA custody. Reference main is labeled reference_source_main, not actual runtime source identity; actual commit/tree/run/shard/image binding is externally verified by the routine verification owner using GitHub/artifact provenance. Source byte hashes alone do not establish that binding. No dropping snapshots/flags/columns to fit. These row/array ceilings deliberately do NOT imply that every theoretical combination fits8MiB: Q4 alone can exceed it (1024 edges ×2 name arrays ×128 elements ×128 UTF-8 bytes >32MiB, before JSON delimiters). The truthful hard ceiling is8MiB; incremental accounting fails closed as soon as a snapshot exceeds4MiB or combined output exceeds8MiB. No claim that all row-cap combinations serialize successfully. A real observed overflow is an unsuccessful diagnostic requiring independent review, never an automatic larger cap, truncation, normalization or weaker facts. A bounded failure record may contain only fixed stage/query/count/overflowcode, not partial raw graph marked collected.
- Exclusive create `/bifrost-results/writer-catalog-receipt.log` O_CREAT|O_EXCL|O_NOFOLLOW, mode0644 explicitly verified (no chmod existing file); regular file/one link/size bounded, fd write+fsync+lseek+readback/byte equality beforeclose; no replace/symlink/stdout receipt. Validate directory supported mount ownership as precedent; existing-file/write/readback/cleanup ambiguity fails. Valid complete raw catalog collection can be stored with reconciliation pending; only failures of the enumerated collection admissions fail the test; all other source/security differences retain complete native observations with reconciliation_pending. Never write `collected` on capped/partial/error output; a raw-complete file left by late IO/failure is not successful collection without actual testcase PASS.
- Source/security reconciliation is a separate gate: unknown extra edges/relations, installed edge/action divergence (including execution-log ORM/migration discrepancy), check/policy/trigger semantics and unsafe existing authority can coexist with raw collection PASS; they keep Stage1 release STOP. Root must independently reconcile a frozen source graph and semantics before owner/guard design. Cross-snapshot structural/revision/identity mismatch fails collection; equality cannot prove unchanged expression bodies. Missing actual named nonskippedJUnit/receipt/readback/candidate provenance or unsupported capture blocks associated-evidence acceptance. No automatic graph repair, migration stamp, owner expansion or permission experiment follows.

## Supported capture and red-capable verification plan (not run)

Current workflow `.github/workflows/ci.yml` SHA256 `8295b675a4dab51dcadba568186303b670cf69a2c0e95f2261cdad4e220e5e3c` does not retain all successful E2E logs; manual pre-pr comprehensive plan can defer all E2E and produce no receipt/JUnit. Passing stdout/property is not dependable evidence. Proposed one `always() && matrix.total != 0` upload step uses existing `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a`, name `writer-catalog-receipt-shard-${{ matrix.shard }}-of-${{ matrix.total }}`, retention14, `if-no-files-found: ignore`, exactly `/tmp/bifrost-bifrost-test-*/writer-catalog-receipt.log` and `/tmp/bifrost-bifrost-test-*/test-results.xml`. Preserve failure diagnostics; no success log globs, new jobs/inputs/permissions or changed tests/targets. Other shards may lack receipt. Owner must retrieve the exact shard artifact and verify actual named testcase plus receipt; absence is STOP, not pass. No intentional test failure to obtain capture.
After separate implementation approval and independent source review, use supported hosted CI/existing isolated test lane only. Commands proposed: `./test.sh tests/unit/test_writer_catalog_receipt_contract.py -q`; `./test.sh tests/e2e/platform/test_writer_catalog_receipt.py -q`; `./test.sh quality api`; clean-candidate `./test.sh pre-pr` (record actual deferred suites), then actual E2E target evidence in required CI. Do not run hostpytest/Docker now, add artificial DDL fixtures, rerun blocked unrelated issues or authorize live roles.
Unit red cases: cycle/self/composite order; duplicate/missing roots/column pair/endpoint; dropped incident Q4 edge detected by joined typef trigger versus legal outside-incident boundary FK; p/u/c Q5 joined consistency; zero constraint nullable fields versus positive missing join; cap and cap+1 everyquery; max identifier/array/bytes and oneover; bool subclass/intbool/null/code errors; direct/pool drift; revision duplicates; fake timeout/cancellation/close failure; error text containing fake secret never serialized; existingfile/symlink/partialwrite/readback mismatch via isolated tempfiles. Actual-driver red seam in the same E2E test, using actual Q0–Q7 returned asyncpg.Records retained transiently before serialization, without any extra SQL: pass its real asyncpg.Record through the contract, then mutate only local copies to wrong native types, missing keys and cap+1 and assert rejection. Real returned rows demonstrate only the classes actually observed. Record per-query count and `observed_empty` when zero; an empty trigger/policy/check/array class does not demonstrate its nonempty native codec. Synthetic unit rows must never be labeled actual codec evidence. Closed code/text/OID-array behavior not exercised by actual rows remains an explicit limitation; a pre-expired deadline must fail before a real query is sent; actual successful connection/transaction close remains the positive driver seam, while cancellation/close failures are injected at the fake connection seam, no deliberately heavy recursive query. No extra receipt query set, target or schema mutation. These are contract tests, not runtime FK/role proof. Healthy PG16 isolated collection is the E2E positive seam; missing roots/types/unsupported catalogs must fail naturally. No tests requiring role/table/schema provisioning are in this package.
Acceptance is **associated diagnostic evidence only**: independently reviewed source + exact candidate/query/SQL hashes, required checks, actual JUnit named testcase executed once, externally verified GitHub commit/tree/run/shard/image and artifact binding, captured readback-matching two-snapshot receipt with reconciliation_pending/remaining securitySTOP explicitly retained. Raw associated diagnostic acceptance does not require completed source-security reconciliation. Stage1 execution remains separately unapproved. Maintainer still needs actual exclusion authority and rollback/side-effect/concurrency tests; C2/C3 remain behind it.

Source harness pins: docker-compose.test.yml SHA256 `0f3a69bd47ed2d4be8225d4d1afdaad681571ed028bb00958000463bcda57e97` (`/bifrost-results` bind mount :454, DB endpoints :360–361); api/tests/conftest.py SHA256 `8e5eb42729bdcff39a578ffcb32956e9509f83a1e75a2ba62f7c47b24a9f2c7b` (URL :42–45, setup/reset :67–101); test.sh namedJUnit/log :378; api/tests/e2e/conftest.py existing boundary health enforcement. The source-only SQL proposal has not been parsed/executed against PG16; independent review must verify catalog columns/casts/recursion and bound arithmetic before implementation.

Remaining gates: final independent package review before implementation; source/semantic reconciliation after bounded observation (not collection admissions); actual repository style/file-boundary verification without invented caps; observed graph fitting the frozen hard8MiB output ceiling (overflow is truthful failed collection); root architecture review of the authorized narrow always-upload scope; actual PG16 codec/catalog-cast validation. None authorizes broader framework, catalog implementation or runtime execution in this source-only task.

Corrected review provenance: `/tmp/bifrost-writer-catalog-receipt-package-independent-review.md` SHA256 `d52e9336b2e75c43a2df7833e92a3b0d3f94f860f2b4a92d842252204317a479` required these structural/privacy/matrix/deadline changes. Its older3b worktree metadata is historical; this revision re-inspected actual docs HEAD913760e4ddbe75d86c1a9a3d18f3a589c74b1e34, clean, no API/workflow/Compose diff from main4ab. Final independent review remains required; this author does not self-approve implementation.

Fixed SQL provenance: hash UTF-8 fenced SQL body plus one trailing LF; Q3–Q7 execution hash uses P body plus LF, then that query body plus LF. Query bodies are fixed by this packet, not user templates.
- Q0 body SHA256 `fade2cb15750e2b299848b1bb77ffba4fbeef7af8e9468069ddfd0ae3f777a65`; execution SHA256 `fade2cb15750e2b299848b1bb77ffba4fbeef7af8e9468069ddfd0ae3f777a65`.
- Q1 body SHA256 `dc1656cd0ddd84907420e74ca2f80f3f25e33985336e49a81abefc335101efc8`; execution SHA256 `dc1656cd0ddd84907420e74ca2f80f3f25e33985336e49a81abefc335101efc8`.
- Q2 body SHA256 `4960eee678dfad793c12f9a3571069d542aa20b5ca779d613ad55da6ca7feea8`; execution SHA256 `4960eee678dfad793c12f9a3571069d542aa20b5ca779d613ad55da6ca7feea8`.
- P body SHA256 `3eb5e00e34465c785bb960d590aba272851d484580a8980e20a1c3b650c02e82`; execution SHA256 `3eb5e00e34465c785bb960d590aba272851d484580a8980e20a1c3b650c02e82`.
- Q3 body SHA256 `d6fe7a47022854d6107e8f24634a475279db8e78706e7d81a23b3a838c882f04`; execution SHA256 `e9cf78d3cf5ddf67fcf69bffe1a847ae54542461e819cfc47077dbbe67b670cb`.
- Q4 body SHA256 `dd2e0aedbb967b5827901aa40716e8fae280743f0bccd9b82738f3ae13023278`; execution SHA256 `dff0b186c1643261fcfb5e87867971b174d04c73eed352e519733c4548f4d8b2`.
- Q5 body SHA256 `8cbeb3d35f9c5c8b973d689e41f3b4d5befa40b9a3a7e57eb53c1f96ca19e9a0`; execution SHA256 `ac61db30e35307b546d042eb1b8ae602f7eb32e5d0023b651495072bb9e316a3`.
- Q6 body SHA256 `04463f4e7c609a0eef277e155d35364db8dc50e235e1939585275d9cbbf80066`; execution SHA256 `ce2dbc1d5163f3d56b0121bc90a226e93f99ade802cd41f62b72bd25e99ae6d7`.
- Q7 body SHA256 `7e1c847b3acd0613184410ddbb442ec88aae2fd9bd2576281eab9f6b5374b3f4`; execution SHA256 `3e7af6b4443809d53a86a1766979eadd6ceeabf947468e69cab459603eee8b10`.
- Q8 body SHA256 `bed514154f5e69c22a4de9ce881886324defe829d3124680b520113da4e722d6`; execution SHA256 `bed514154f5e69c22a4de9ce881886324defe829d3124680b520113da4e722d6`.
