# CRED-P1: current-main integration

Package `CRED-P1-MAIN/v1`, 2026-10-17. This records a credential prerequisite,
not Rust lifecycle ownership or common-schema acceptance. Local source review,
supported tests, literal pre-PR, live frontend/API proof and cleanup passed.
PR publication is verified; normal hosted CI remains queued.

## Source and authority

Platform main is `e42999395231ebc536711e5015422edfb75613a7`; workspace main is
`0bec57e53477553d10b11a70d12cb859c465515f`. Both prerequisite merges remain
ancestors. The [workspace audit](rust-core-mvp-workspace-boundary-0bec57e5.json)
finds 2,459 authored Python files, zero forbidden imports/parse errors and an
empty internal-import allowlist. Its Python blob manifest is unchanged from
the preceding workspace audit; the newer commits change PowerShell/docs/CI.

[PR #1037](https://github.com/Midtown-Technology-Group/bifrost/pull/1037) is
open/unmerged at the published integration candidate
`6e56a72affdb828f3249e320f704c666ce775298`, tree
`61df67a63482349ca10fc444edc879cc2443b3f9`. REST readback confirms its base
is current main above and merge conflicts are resolved. Diagnostic work was
first published on `test/cred-p1-main-integration`; the qualified candidate is
now on the PR's `fix/runtime-sdk-ingress` branch. Local ownership stays with
`/home/thomas/src/bifrost-runtime-sdk-ingress`, branch `fix/runtime-sdk-ingress`.

The main merge preserves all sixteen execution/bypass CLI gates; exactly two
use the dedicated SDK dependencies. Main's `src/core/auth.py` is unchanged.
The existing optional manual pre-PR input and main's Sonar inputs coexist;
no CI threshold, selector or required check is removed. The unlanded CRED
migration now follows `20261012_work_delivery_started`; its upgrade/downgrade
bodies are unchanged. The static graph has 331 revisions and one CRED head.
This separate native CRED history must not be applied alongside the held common
table-creation candidates. Their consolidation and least-authority issuer
adapter remain explicit R2 gates.

## Measured regression and correction

Current main's two SDK reads admit execution credentials or platform/provider
bypass principals. The older CRED fallback called only `get_current_user`,
which would have restored ordinary-user access after the merge.

The correction authenticates legacy credentials, then calls the unchanged
`get_current_engine_or_bypass_user`. Dedicated credential admission, operations,
renewal, caller snapshots and grant policy are unchanged.

| Evidence | Exact source | Observed result |
| --- | --- | --- |
| Dependency regression before the correction | `8c286df7b25a960f40e4a463e019d5e4d691a350` | 8 expected failures and 8 passes: both aliases wrongly admitted ordinary, external, role-only and embed principals. |
| Independent real-HTTP reference | `ad5e88837af84bb54335763137b9309b303ab169` = main plus one probe | All 10 cases pass: both reads return anonymous401, ordinary/external403, admin/provider200-null. |
| Corrected credential/guard selection | `a420fa9ff4e7c8bad7faf716ee6e158d214c1ab3` | 282 pass, zero skips: 272 codec/grant/ingress/refresh/guard cases and the same 10 HTTP probes. |
| Adapted actual SDK/HTTP/OAuth selection | `6e56a72affdb828f3249e320f704c666ce775298` | 30 pass, zero skips: all 20 existing HTTP/SDK cases plus the same 10 probes. |

There are **302 unique selected cases**, not 312: ten HTTP cases overlap.
The 272 unit/guard cases ran on a420; their source inputs and the HTTP probe
are byte-identical at6e56, which changes only the existing HTTP suite.
This retained selection is not a transferred full-candidate CI pass.

The identical reference/candidate HTTP probe is SHA256
`d91e7288281060b619ffe75c9924fac7ed74be3b173ca8a011a4d41e975d096d`.
It uses signed synthetic session credentials, actual API routes and a private
cookie jar with redirects disabled. Missing integration names keep this access
probe from obtaining resource data; it does not prove a populated SDK read.

The existing HTTP suite now asserts ordinary/internal and external caller403
before config reads or OAuth changes. Positive legacy cases use the cohort's
distinct, existing non-admin provider user and real provider organization.
Their cross-target scope, actor ID and actor organization are explicit. The
ordinary runtime caller, its source, grant and policy are never upgraded.
Dedicated runtime/external and OAuth positives remain. Oversized ordinary
credentials deny; oversized unreserved provider credentials still work;
reserved markers cannot fall through to that bypass authority. The 32 emitted
warnings concern existing WebSocket/Uvicorn and Redis-close deprecations; none
were suppressed or treated as failed expectations.

## Evidence custody and review

Supported host is `pve-t340.netbird.cloud`, VM101
`bifrost-platform-test-debian13-01`. Candidate checkout is
`/home/dev/bifrost-cred-p1-main-8c286df7b`, native project
`bifrost-test-aaf1c780`. Independent reference checkout is
`/home/dev/bifrost-cred-p1-access-reference-ad5e88837`, project
`bifrost-test-cd372b80`; that reference project's containers/networks/volumes
were removed and read back empty. Private evidence remains on guest and under
`/home/thomas/src/bifrost-rust-mvp-artifacts/cred-p1-*`.

| Retained result | Log SHA256 | JUnit SHA256 |
| --- | --- | --- |
| Diagnostic 16 cases | `013fb0d393a0be186974726fb04096e5ed720d60584e9dbdf287092c15e1cd0e` | `81b061471789bf2ecae4514320c4d732dbd550e82d246c3c67d2ecbe450c3de4` |
| Main HTTP reference | `b1e3fcf917672f688160064ba4678ec04bcc4f74dadf927d2a43f5aec848b993` | `45f3e2be0654fa7ebc82d245ee2fc366470b54e77fd0594601640d88f318ef81` |
| Corrected 282 selection | `d54ed0d6d6c68c0d8b8d0e45852cd51229a01f6c74797b40fd506dfc01d379d0` | `ce93c4dc24ade7d3dad68b63a38aacadc24852a7d3248c6639df1973ca873283` |
| Final 30 HTTP selection | `67314a94dafeecdfcc9f4e6800c180121256a898582248f779a20c67b0992975` | `a6eb242bcbc422838579375658be2ab95ad79f06eaec2f9fe444c557dc039619` |

Independent Sol source review accepts the merge boundary, guard fix/matrix and
HTTP adaptation. Reviewed implementation SHA256 is
`7d1d7b405885aadc83a752b05ae97f764786df91da7f3418be2d83a63c701eb7`;
matrix `cd85861b22bd5b30e9de69951f947597bb91ef60dc91c095919afb2f1c9221a0`;
HTTP suite `e5418c96f070468b1f9884bb087dcbffe991ccd6b571f6be95dfa151f395c618`.
No reviewer imports/tests/SQL are claimed. Historical3918 review and CI remain
bound to their original source, not this candidate.

## Final candidate verification and cleanup

Literal `./test.sh pre-pr` on clean6e56 exited0. Retained log SHA256:
`4d0064bc044d4ca57b3df968275d8d2a7a29b912d2951f00717dcd695cbbc539`.
API quality and generated-contract checks passed; the repository's trusted
planner deferred broad suites to normal CI. That deferral is not a CI pass.

The separate debug project `bifrost-debug-aaf1c780` was brought up with the
repository's supported port mode. Its printed endpoint was
`http://localhost:38197`. The same ten access outcomes were exercised through
the actual frontend proxy: anonymous401, ordinary/external403, and
admin/provider200-null on both routes. Redirects were disabled. Credentials
were short-lived synthetic session tokens held in subprocess memory; no real
vendor was contacted. This is an access-boundary proof, not workflow execution.

Debug API image:
`sha256:f10121439ec272849439e3e574fa5e2c68a675b46a2b42d80b0e34578b5034b8`;
frontend image:
`sha256:908dc326731c62be9778522703a862ddd291f29e4070858145d4aea86833949c`.
The retained `debug-access-6e56.json` receipt is SHA256
`cb7fc07e291f0406eca58b1d015396c00b769d66d5520629c1ba3110812f8a5a`.
The QGA environment initially lacked HOME and debug preflight stopped before
mutation; running under the existing root login environment corrected that
environment defect without changing scripts or expectations.

Both candidate native/debug projects were removed with their supported down
commands. Successful Docker readback found zero project containers, networks,
volumes or one-off probes; all recorded attached task volumes were removed,
and all 143 preexisting containers and 2,567 volumes were preserved. Receipt
`cleanup-result-6e56.json` is SHA256
`2790be981b54c120c96b402a06b99cd81cff78737060c088ffe796fbe66530f7`.
Root debug credentials/configuration and shared caches were preserved.

VM shutdown ownership was explicitly accepted from the Go owner, who confirmed
their two CLI checkouts had already been removed. A final successful guest
inspection found no running containers or test/build processes. The pinned R1
Python image61a673bed and Rust image0dc3dbe0d remained intact. Graceful VM101
shutdown completed; `qm status 101` read back **stopped**, 2026-10-17T03:21Z.
The endpoint above is therefore no longer running.

Automatically triggered attempt1 runs on exact6e56 remain queued as of this
record: CI38998308635, CodeQL38998308515 and queue authorization38998308717.
GraphQL review/check refresh was rate-limited; these observations use REST.
No duplicate dispatch or benchmark rerun was requested. R1 source560e3decd
CI38996089958 and Rust Core38996091192 also remain queued and separate.

## Maintainer-directed provenance and remaining gates

This is an **agent-recorded authorization and provenance record**. Principal:
Thomas Bray, `MTG-Thomas`, GitHub ID87775189 (fresh provider readback). Agent:
Codex/Sol, accountable architect/implementer, with independent Sol source review.
Scope is the authorized narrow CRED prerequisite and ordinary current-main
integration supporting the Rust MVP. New owned commits are merge8c286df7b,
a420fa9ff and6e56a72af; inherited main history is reused, not agent-authored.
Existing BiFrost source/authorship/license notices are preserved; no external
implementation was copied. GitHub reports final6e56 signature verified/valid;
that identifies the publishing route, not a personal DCO certification or
human review. No personal sign-off is invented.

The common `R1-CANCEL-SQL/v1` authority request remains unanswered and held.
No common candidate schema, four-role profile, test logins, credential-writer
role or common runtime was activated. No merge, deployment or live vendor use.
The stopped custom CI Git-resolver proposal, #1017 campaign and other exhausted
runtime campaigns remain stopped. No full runner-credential isolation is claimed.

Still unproved: common installed authority/issuer adaptation, real Rust Start
and terminal ownership, actual Python workflow/agent execution, mixed writers,
physical custody, rollback and C2/C3. These credential mechanism tests do not
complete those product gates or justify a final CONTINUE decision.
