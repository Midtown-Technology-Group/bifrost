# Rust MVP: COMMIT-loss controller contract

Status: frozen design for source authoring only, 2026-10-04. The controller is
being implemented in `test/workflow-commit-fault-controller-source`, based on
`9b54e910e8e5380150f8ee26cfa1f13e5bec7424`, carrying the reviewed
prerequisites on the earlier `c911b46baa7260ed1492bfbe5e1295c6db9eb410`
source-check baseline. Full source review and supported
execution remain required. This document grants no runtime, authority, role,
schema, merge, deployment or vendor-use permission.

The selected experiment runs the real Python consumer and Rust Result operation
against the existing Alembic-managed schema. For each lane it must witness a
consumed COMMIT transport error, the exact assigned transaction independently
committed, and complete post-write rows. It must preserve ambiguity and avoid
replay. This is one atomicity experiment; it does not establish general Result,
HTTP, event, mixed-writer or runtime-extraction parity.

The sections below retain the exact reviewed design packets. Their statements
requiring distinct design review record the freeze gate that has now passed;
the architect separately released source authoring after each review. Those
reviews do not discharge full source or runtime review. The current native
roster contains 217 labels (165 normal, 52 failure), with 18 frontend probes.
The metadata-profile section records its original two config-render argv
amendments. The current selections below supersede its roster lineage; the
remaining retained packet bodies are unchanged. The current cleanup selection
explicitly supersedes their native-command count bounds of 207 with 217;
all other field types, artifact sizes and clocks stay unchanged.

Independent review fingerprints, in section order:

- `680fade2ec45369289b0206be0d4aba6aa9b43025d810b4180ad1c7f7fa01a28`
- `a06c32056d4e0339477cf8919f9d894277db349c7236f8a8a99085f914e0b232`
- `c0685f23957a4ac2a22f2175574e1cb8577685c85462643283a887c466611ec4`

All stopped campaigns remain stopped. The separate four-cycle Rust source-check
campaign passed formatting, strict all-target/all-feature Clippy and 27 default
tests in [run 37185003270](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37185003270).
It did not run this experiment. See [verification disposition](rust-core-mvp-verification-disposition.md)
for the historical failures and remaining acceptance gates.


## Current source-authoring selections

Platform main is `c45725f82f0961ce45c1dc7e4928402b44176882`; workspace
main is `848134becf9e8fdb930efc86872daa62fdeb93cc`. Both prerequisite merges
remain ancestors. RFC #1011 remains provisional at
`93ec9421b23b0b4a1493ab45f15f55ded3377d2e`. These are fetched source refs,
not deployed identities.

The controlled item now lives at
`api/tests/diagnostics/workflow_commit_fault.py`. Its exact-byte relocation
keeps it out of ordinary automatic `test_*.py` discovery while preserving the
mandatory explicit invocation, real ancestor conftest and all assertions.
No test skip, planner rule, helper import or ordinary test command changed.
The file SHA is `9a4eb183aafa42625f225a18b49a93e5704938ed00e1f6b5583e4826f9675592`.
Its source review fingerprint is
`f4ebe6743e314a02636f3a848f997d1c6ded742408b89eb57a63038a98719dbe`.
Actual standalone module/conftest identity still needs runtime qualification.

The existing API image supplies `BIFROST_VERSION=unknown`; qualify the actual
image configuration and inherited runtime value rather than manufacturing an
override. Python/observer runtime environments therefore contain exactly
28 selected keys; their supplied environment files still contain 27. Rust
retains its single key. The observer now requires eight control families,
including the actual environment guard's positive case and four negative
cases. These operate on a private diagnostic alias; genuine `os.environ`
stays unchanged and original failure precedence survives restoration.
Source review fingerprint:
`da7712a61a432d849d66ecc1c2e45e6df20927fb66b687070e9f1b30c522b37a`.
The controls have not run in the supported environment.

The image-acquisition roster v0.1.24 fingerprint:
`b50e2428a47a665e316c20f5d08db087dfa147ede542304b5f8fe1123bc2150b`.
It retains 207 labels and 18 frontend probes. Initial image enumeration now
retains all immutable IDs, including untagged/intermediate images, under the
existing stream caps and a stricter 4096-record bound. A newly built tag must
not confer ownership of an initially present cached ID. Initial tags must also
be absent. Exact source/build/config joins and an exclusive supported daemon
remain necessary; unknown ownership fails without deletion or retry.

API/toolchain build tags are selectors for their first image inspection only;
later use and disposal require measured immutable IDs. Complete cold Cargo
work precedes the original 180-second stack interval. After stack startup and
qualification of all 11 service containers, one joint image inspection measures
the owned API image and borrowed PgBouncer image before frontend probes.
The current Compose recipe uses `edoburu/pgbouncer:latest`; record the actual
named digest without inventing a pinned digest or signature claim. Actual
frozen version, platform, process and INI checks remain strict. PgBouncer is
not added to task-owned image removal. Distinct design review fingerprint:
`b9fc79e822541a3ef56f9e08c98a5adca5a3fedd5ee57a2ee7cd7bf3bc75d457`.
No command, capture cap or clock extension was authorized.

Only the sole controlled target receives host command-local
`BIFROST_TEST_USE_CLEAN_BOOT=1`, alongside its existing skip-build option.
Normal pre-PR behavior stays unchanged. This selects the existing runner's
one-shot fresh-stack preparation; otherwise it resets PgBouncer/database after
the retained identity/schema observations. Before this invocation, require
actual marker absence without following symlinks, original task-owned log
folder identity and genuine fresh-stack qualification. Never delete a marker
to obtain admission. Afterwards, require the actual zero-byte regular one-link
marker with source-supported ownership transitions and unchanged full
frontend/database lifetime evidence. Neither marker nor flag proves behavior.
Do not propagate the selector to actor environments or use it for a second
suite. Distinct design review fingerprint:
`3af16d081842aae3c3f4ef99247b3d2a3c44996443f1c5c60843397ae23faf52`.

The external task-created log directory is an unresolved cleanup gate. The
unchanged runner can recursively change its owner to UID 1000; teardown exports
logs and removes Compose resources but leaves that host bind-source directory.
A different unprivileged host user may be unable to remove nested contents or
the directory itself under a sticky `/tmp`. Actual hosted permissions and
removal outcomes are unmeasured. Residual or unknown logs prevent overall
`disposal.complete` and `passed`; Docker resource absence does not settle them.

The extra host-privileged removal proposal remains rejected: killing or reaping
a nonroot `sudo` monitor would not prove its root command stopped. No host
privilege supervisor, `sudo` operation, retry or larger deadline is selected.

Its replacement is one owned cleanup container, using the existing Docker CID
create/qualify/start/terminal/kill/remove/absence custody pattern. Direct GNU
`chown` restores only the task-owned log tree to the actual numeric host
UID/GID. The host then performs bounded no-follow removal. The container has
no network, a read-only root filesystem, only CHOWN/DAC_READ_SEARCH capabilities,
and one writable bind of the qualified log tree. It uses the immutable qualified
API image without pulling or invoking its API entrypoint. Actual source/software,
daemon, image, mount, membership, ownership and writer-settlement checks are
mandatory. Its Docker client exit is distinct from container settlement.

All log writers, including stack-down/export, must settle first. Actual terminal
exit, unchanged root/member identities with restored host ownership, and
independent container removal/absence precede host deletion. Final log-root
absence is required. An inaccessible census, unsupported image/daemon, failed
restore or remaining file still prevents successful disposal; no chmod rescue
or second attempt is selected. API image disposal follows helper settlement.

The replacement explicitly adds six normal and four failure-only methods to
the original 207, preserving those original row objects. Current roster v0.1.26:
`e5b64ccbbaf147866f4498660524e0508dd57969453e41f60a9a8da6bc25882b`.
Parent producer/disposal native counters now admit at most 217 and reject 218.
The added calls consume the original cleanup deadline; business lanes, frontend
probes, capture limits, artifact schemas and sizes are unchanged. This corrects
an omitted task-file cleanup mechanism before the first controller run; it
waives no observed failure or uncertainty. Distinct design review fingerprint:
`9cebbf3b03370403b4b6e77f6f93917033425ddc5fbb89a93fbddbe396253576`.
Source authoring is released; implementation and runtime proof remain pending.

The current roster is v0.1.27:
`abba56575b0ce5ac261b26c4c327c44d7daf16c395902214f2b8c237caa4435d`.
It changes only the existing initial, pre-PR-final and target-final volume
censuses to unfiltered Docker volume listings with closed name/driver/scope
records. The other 214 row objects, all counts, deadlines and capture bounds
remain unchanged. Source-declared anonymous mounts need actual custody even
when they have no Compose project label; project-filtered absence is insufficient.

Require a complete initial volume-name set before any acquisition. Preserve
preexisting volumes and reject their adoption, including actual task-container
mounts into that set. Retain every qualified named or anonymous mount's actual
name, driver, destination and access mode. Each census admits at most 4096
complete unique records; malformed, duplicate, truncated or unsupported output
fails rather than supplying a positive prefix.

After genuine pre-PR and all its writers settle, the complete volume-name set
must equal the initial set before fresh target acquisition. After actual target,
helper, container and owned-volume disposal, require the same equality and
absence of every measured task mount name. Use only the original scoped
teardown; never prune or delete unrelated volumes to force equality. These
checks retain their original deadlines and causal placement.

Set equality observes registered-volume absence. It does not reconstruct
already removed pre-PR anonymous identities, prove immutable same-name object
continuity, certify physical disk/layer cleanup, or justify invented historical
resource counts. Missing required source, ownership or disposition history
still prevents completion. Distinct design review fingerprint:
`67c61c751e5dbfabee6a298f878c5aa04784aace57c7c046c4fb68198ad1e6c7`.

The selected v0.1.35 count scope explicitly amends the earlier blanket history
wording. Resource counts describe applicable parent admissions, source-associated
current identities and actual admission gaps, supported by private per-identity
records. They do not enumerate every already-removed opaque pre-PR object.
This limitation applies to the unchanged public schema, including its
`complete` field; neither a count nor success certifies exhaustive opaque
history, total daemon emptiness, physical layer cleanup or unseen descendants.

A missing expected acquisition, lost handle, unexplained mount, missing source
reference, failed ownership join or unresolved actual disposition still counts
as unknown or fails admission. It cannot disappear because no identifier was
returned or a dictionary contains only successful acquisitions. Unknown is zero
only after complete applicable observation and individual ownership/disposition
joins. An unavailable or incomplete observation stays null. Earlier source or
custody failure remains latched even if later snapshots match.

Every actual qualified target volume name and all its CID/image/destination/
driver/access references participate in the ledger. Shared references count
one object but each reference must join. Initial refusal to adopt preexisting
names, pre-PR-final preservation, late final preservation and each task name's
actual absence remain separate requirements. Cargo/target custody stays separate.
No matching scalar totals or endpoint sets supply missing per-identity evidence.

Opaque pre-PR still requires its genuine source-qualified invocation, native/
process/FD settlement, supported scoped cleanup and complete independent
container/network/volume checks before fresh acquisition. Observed unresolved
resources or missing phase evidence prevent completion. The experiment supplies
no historical zero/count for unselected opaque acquisitions. Actual trusted,
exclusive disposable-job admission remains mandatory. Required controls must
reject acquisition gaps, missing source/mount references, conflicting drivers,
initial-name adoption, incomplete censuses and missing/wrong absence joins,
including a missing join hidden behind otherwise matching totals.

Selected design and distinct review fingerprints:
`587dd6a571c4ab07b55d445797c3e4f212ede2d7294f1c9839d3d85dbc2c52be`,
`340a6149f1e0b683ca8ac6c0d112a13a93ce93feae25558f95645b5fc7fbc4f6`.
This changes count interpretation explicitly before execution; it waives no
observed failure and changes no command, clock, artifact size or business lane.

The architect retains this finite experiment despite its substantial supervision
cost. The current controller is 6532 lines; this is a maintenance burden, not a
Rust benefit. Source review and the first supported invocation must test its
feasibility within existing bounds. Infrastructure failure is not evidence that
Rust atomicity failed, and it must not trigger indefinite blind retries or budget
growth. Independent cost review fingerprint:
`8e2e827c22108531420aa54bbccc64547515ce48be4c9bc25176be9cccdbbd5e`.

All selections above permit bounded source authoring only. Frozen parent source
`2b5f6e00bc3cc716097e5fbfe4bf87d1924f2b208bb02e7bb8f9635291f7bcde`
still blocks completion conservatively and needs the selected per-identity scope
and controls before a new exact-file freeze. Full independent source review,
supported execution, actual custody and cleanup remain outstanding. The earlier
green 27-test run does not qualify these newer source changes.

---

# F4 metadata profile selection — root DESIGN v0.1.23

Source baseline c911b46baa7260ed1492bfbe5e1295c6db9eb410, current main c45725f82f0961ce45c1dc7e4928402b44176882. Builder found missing selected test-runner metadata before edits. Root selects explicit --profile e2e --profile test --profile client --profile client-check immediately before config in compose.base and compose.resolved only. Exact two argv amendments are frozen in parent-command-roster v0.1.21; no row/count/condition/clock/error/capture requirement changes. Previous v020 roster is lineage. New roster supersedes only these two argv inputs.

These are all current profiles in docker-compose.test.yml. Complete metadata is needed for test-runner environment/source/UID recipes and pre-PR resources. Both original and resolved models must render with the same profile selection before the original allowed observer-bind inverse equality check. No wildcard or ambient COMPOSE_PROFILES authority. The parent environment must omit ambient profile selectors, preserve already selected closed environment and reject unsupported source profiles; no source-model/key/env/DSN/ownership guard is weakened.

This selection applies ONLY to config --format json, never up/run/build/down service selection. Actual original test.sh stack e2e startup, explicit test runner, pre-PR client/client-check services and exact 11 post-stack singletons remain unchanged. Metadata inclusion is not a runtime admission or authorization to launch those services. 207 labels=159normal48failure; 18frontend, fixed budgets/native lifetime/custody/cleanup unchanged. Missing/extra/unsupported service/model identity still RED. No fake installed Compose proof or Result campaign revival follows. Distinct DESIGN review required before author continues affected metadata source. No runtime/profile/parser/helper/CI execution or repository source edits made by this amendment.
---

# F4 safe publication — root DESIGN v0.1.25

Select the missing parent export seam for source authoring. Baseline c911b46baa7260ed1492bfbe5e1295c6db9eb410; current main c457; two real required lanes remain python then rust. Distinct review required before affected publication source continuation. No runtime or authority release. This is F4-specific, not adoption of the five ordinary Result artifacts or their schemas.

Publish exactly three closed compact ASCII JSON documents, each ending one LF: fault.receipt.json <=16384 bytes, producer.json <=65536 bytes, disposal.json <=16384 bytes (caps include LF). No raw pytest XML, log, configuration, endpoint, row snapshot, error text, SQL, credential, token, payload hash, container/process ID, private path or private capture. No JUnit artifact. Parse the private native JUnit only to verify the selected one item and source-declared control/2-lane properties; it never becomes the exported receipt or transport/fate authority. Partial/malformed XML and missing properties remain failure. Actors' original properties and schemas are unchanged.

fault.receipt.json retains exact five roots from the held proposal: schema='bifrost.test.workflow-commit-fault/v1', case_id='r-tx-lost-commit-response', source_sha256, lanes, passed. source_sha256 is exactly {source_manifest,rust_binary}, values null until separately observed/qualified, else lowercase64 SHA256. source_manifest hashes the canonical sorted list of fixed source-relative software paths/bytes digests used by the selected source/recipe/driver/lock gates, including current controller/workflow/four actors/SQL adapter/contracts/harness/fixtures/consumer/database/SDK/build inputs. Never mix environment, customer records, DSN, claim token or expected values into it; no embedded self-hash constant. rust_binary is actual externally qualified built binary digest, not a source hash or echoed value. Manifest grammar/path membership and actual installed source joins remain part of full SOURCE review; the digest alone is not that proof.

lanes is exactly {python,rust}. Each lane has exactly frontend_matches,transaction_witness_matches,selected_writes_observed,listener_returned,armed,upstream_forwarded,downstream_dropped,client_outcome,server_status,row_relation,exposure,cleanup_complete. This deliberately supersedes the proposal's public pg_backend_pid/xid8 fields with transaction_witness_matches: exact PID/xid8/server tuple stays PRIVATE and must still be independently joined and used for original-endpoint fate. Unknown observation booleans are null (never fabricated false success); observed bools are strict bool, counters null or strict nonnegative integer <=2^64-1 excluding bool. client_outcome null or exact {commit,error}; commit not_dispatched|acknowledged|error; error null or exact {family,code}. Genuine existing error projections: python_connection_lost/08003; rust_io/unexpected_eof|connection_reset|broken_pipe; other/null. Preserve source classifications, no class matching by name/repr or arbitrary cause traversal. Generic other does not prove the selected fault. server_status null|committed|aborted|in_progress|unavailable, mapping only exact PostgreSQL 'in progress' to safe in_progress; row_relation null|all_before|all_after|mixed|unavailable. No normalization of row cells/fate or timestamps to make comparison pass.

exposure is true only after real source/physical/selected-write/arm/positive opaque-drop/failed consumed-COMMIT joins plus independently committed exact assigned xid and full all_after rows. Each lane is mandatory; aborted/all_before may describe ambiguity but cannot satisfy this exposing experiment. cleanup_complete is only observed source/actor/cohort lane settlement, not parent EMPTY. passed is strict bool and true only if BOTH exposing lanes, every source/profile/native/prePR/schema/clock/control/identity gate, selected item, complete parent custody, independently verified cleanup and original successful exit all hold. It cannot be computed from actor properties, local output delivery or artifact upload alone.

producer.json schema='bifrost.test.workflow-commit-producer/v1', exact roots schema,source,stage,commands,primary_exit,complete. source exact {head,tree,main,workspace}, each null before qualified else lowercase40 software Git SHA; main/workspace are fetched authoritative refs, not claimed deployed refs. stage null or one exact declared roster label. commands is actual started-command records only, <=207 in causal launch order; exact keys label,settled,native_exit,stdout_eof,stderr_eof,group_settled,fd_settled. label is a unique fixed roster member, never argv or source-provided arbitrary text; boolean fields null until measured, then strictbool; native_exit null or integer0..255; each record encoded <=256bytes. Native signal is projected through the fixed conventional 128+signal value only after actual native settlement; unknown unrepresentable status is incomplete. primary_exit null before outcome or strict integer0..255. complete is true only for source-qualified complete command/read/settlement evidence, not simply zero exit. In-process controls/IPC do not invent native-command records.

disposal.json schema='bifrost.test.workflow-commit-disposal/v1', exact roots schema,primary_exit,cleanup_exit,complete,processes,resources,private_captures,credentials_disposed. processes exact {started,settled}, actual counts0..207. resources exact {containers,networks,volumes,images,cargo,files}; each exact {admitted,removed,remaining,unknown}, strict nonnegative count (unknown can be null when census itself unavailable, never zero by default). Include creation-gap objects as unknown when source/ownership cannot be admitted, never adopt by desired name. private_captures exact {closed,removed}, credentials_disposed nullablebool. complete requires every applicable qualified owned object settled/disposed with current independent inspections, no unknown/remaining, capture/credential closure and no cleanup failure. Counts summarize measured identities held PRIVATE; arithmetic alone is not ownership proof. Preexisting objects are excluded only by proven initial/current joins, not inferred absence. Image/Cargo/layer disposal limits remain honest; no total Docker daemon EMPTY claim.

Create an initially absent task-owned safe output directory with exclusive regular non-symlink files, validate closed types/keys/size after serialization before any publication, and retain only these files for the existing pinned upload-artifact action. Safe directory owner retention through upload is explicit; it is not secretly counted as removed private capture. No recursive/glob upload of a parent directory containing captures. Private raw captures remain original0700/0600, independently disposed; unknown settlement is failure even if upload succeeds. Artifact output path is nonsecret but no secret-bearing command/config may enter it. No extra business lane, native roster label, new clock or capacity growth; publication/validation consumes original controller lifetime. Overflow/write/flush/close/upload failure never yields full success; original first failure remains. Missing artifact/outer job interruption is missing evidence, never EMPTY.

Source author must implement inert same-validator controls for unknown/duplicate keys/types, wrong enums, unknown-as-zero, excess207/duplicate labels, output caps, sensitive extra fields and premature passed/complete, keeping actual controls distinct from runtime parity. Workflow always-publication may upload only already validated safe regular files; no raw fallback when a safe receipt is missing. Exact three file allowlist/action pins and final source-manifest membership need full independent SOURCE review. No global event/log/Redis redesign, schema/role change, merge, deployment, vendor use or Result campaign revival.

## Exact scalar grammar (supersedes ambiguous prose only)

Lane field mapping: frontend_matches, transaction_witness_matches, selected_writes_observed, listener_returned, armed, exposure and cleanup_complete are strict bool or null until their actual predicate is observed. upstream_forwarded and downstream_dropped are strict integer0..18446744073709551615 or null until actually measured; bool is not integer. client_outcome/server_status/row_relation have the already enumerated object/enum/null grammar. No selected_writes_observed count or alternate field interpretation. passed is always strict bool, false while any mandatory evidence is unavailable.

client_outcome=null before an actual terminal source classification. Otherwise exactly commit+error. Allowed error pairs are {family:'python_connection_lost',code:'08003'}, {family:'rust_io',code:'unexpected_eof'|'connection_reset'|'broken_pipe'}, or {family:'other',code:null}; family is never null. error may itself be null only with acknowledged/not_dispatched or unknown error classification; commit:error plus error:null cannot prove exposure. A successful lost-response exposure requires commit:error and that lane's source-supported error pair; no acknowledged/other/null pairing satisfies it. Unclassified failure is preserved as incomplete, not normalized to a qualifying transport error.

Disposal primary_exit/cleanup_exit are null until actually available or strict integer0..255; complete is strict bool and false until every required predicate holds. processes.started/settled are strict integer0..207, with settled<=started; these count parent-admitted roster native commands only. They do NOT count opaque descendants inside Docker/test.sh/build commands or prove those descendants settled. Parent process groups, returned CID/process/socket handles and actual owned-object inspection must independently establish applicable settlement; a count equality cannot discharge it.

Every resource group's admitted/removed/remaining/unknown is strict integer0..18446744073709551615 or null while that actual observation is unavailable, excluding bool. These scalar representation bounds do not authorize any new resource or raise the original acquisition/roster capacities. No default zero from failed/missing census. complete requires applicable nonnull counts, removed/admitted evidence reconciled by PRIVATE identity joins, remaining=0 and unknown=0 with actual inspection; counts alone are insufficient. private_captures.closed/removed and credentials_disposed are strict bool or null until observed. Complete requires each applicable capture/credential predicate true, including proven zero-resource custody where no such acquisition happened.

This is the finite H1/H2/H3 correction to independent review398a64ad79346ff76127dac840cc68ecc2e1e84d7bfb7fc5471c2226ce1c58d2. All three artifact names/caps, root/nested keys, first-error priority, actual COMMIT/XID/full-row/identity/source/cleanup gates, clocks/207 roster, private-only raw captures and publication ownership remain unchanged. Distinct review required before affected publication source continuation.
---

# F4 parent native capture allocation — root DESIGN v0.1.26

First explicit parent-capture allocation; this is not an increase to the already selected actor/reader/IPC limits. Current source c911;207 roster v021a8c50 and source authoring only. Default parent-admitted native command stdout and stderr each have16MiB hard retained-byte limit; all parent native captured streams combined have64MiB hard limit. Charges are actual bytes, not Unicode characters, and remain charged after private file removal; no budget reuse/renewal between commands. Limits include framing if present. These are initial independent F4 selections; no assumption of measured fit or borrowing prior Result execution evidence.

Existing narrower limits override default: observer/relay/Python/Rust actor stdout and stderr64KiB each unchanged; namespace/source reader stdout8192ASCII JSONbytes plus exactlyoneLF (8193 rawbytes), no extra whitespace/secondrecord, stderr64KiB; control4096 and snapshot65536 frame caps unchanged. Git cat-file whole software/migration blob protocol remains admitted under default parent16MiB stream plus aggregate64MiB: it may not be truncated to64KiB. Preserve exact binary records/hash/source joins; partial batch can never qualify a source.

Allocate bounded buffers/private0600 stream files incrementally under owned0700 parentcapture directory; read chunks with actualremainingbyte checks before retained writes, never unbounded communicate/read-all or unlimited spool. Reaching cap while EOF is not yet observed retains original overflow failure, stops new positive command admission, and triggers independent selected protected kill/drain/reap/FD/group cleanup under original whole/emergency deadlines. Never discard tail and declare complete, ignore bytes, reuse capacity, weaken bothEOF/nativewait/FD predicates or restart. Failed creation/read/write/close is independently red. Original firsterror survives cleanup. Capture overflow is an honest failed experiment, not permission to expand capacities.

This bounds the parent-owned capture streams ONLY. Internal Docker/test.sh/build descendants and their raw disk logs/layers are not independently bounded by this byte counter; no disk-quota, totalmemory, childtree-empty or daemonlayer-disposal guarantee is invented. Existing test.sh logging limitation remains explicit in source/evaluation evidence, and resource/process/source custody still needs actual inspection. No host runtime/helper execution, new native command/actor/clock/public artifact/schema, scope authority or ResultSTOPrevival. Distinct DESIGN review required before affected IO source continuation; controller SOURCE/execution remain independent gates.
