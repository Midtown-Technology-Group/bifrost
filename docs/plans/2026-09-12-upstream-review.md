# Upstream modernization review dispositions

PR #708 integrates upstream modernization with fork behavior preserved. CodeRabbit
skipped its review because of the file count; its success status is not a review.
The local full gate passed on `aff63230e`; subsequent repairs require a new gate.

## CI cleanup lock

The reliability scenarios and backend shard tests passed, but later host cleanup
could not reopen `test-stack.lock` in the container-writable results directory.
Move both host locks into per-worktree Git metadata, outside container mounts.
The repository gate now checks mutual exclusion and command dispatch with an
unreadable legacy lock in a sticky results directory. No lock is bypassed, and
no results-directory permissions are weakened by this repair.

## SonarCloud security and reliability findings

The original analysis reported 17 security/reliability findings. The table below
records the disposition of every one; style/complexity suggestions are separate
from the failed security/reliability ratings. No analyzer rule is disabled.

| Finding | Disposition |
| --- | --- |
| [api/scripts/scheduler_fixture_server.py:207](https://sonarcloud.io/project/issues?id=Midtown-Technology-Group_bifrost&issues=AaCX0uLjQzi-Va3rw2bu&pullRequest=708) | False positive: the fixture emits JSON serialized by `json.dumps` with `application/json`, or JSON-encoded SSE frames with `text/event-stream`. Neither response is HTML; HTML escaping would corrupt the protocol. Requests cannot select the response media type. This server is test infrastructure. |
| [api/scripts/scheduler_fixture_server.py:303](https://sonarcloud.io/project/issues?id=Midtown-Technology-Group_bifrost&issues=AaCX0uLjQzi-Va3rw2bt&pullRequest=708) | False positive: the fixture emits JSON serialized by `json.dumps` with `application/json`, or JSON-encoded SSE frames with `text/event-stream`. Neither response is HTML; HTML escaping would corrupt the protocol. Requests cannot select the response media type. This server is test infrastructure. |
| [client/src/components/agents/RunCard.tsx:99](https://sonarcloud.io/project/issues?id=Midtown-Technology-Group_bifrost&issues=AaCX0vIKQzi-Va3rw2gB&pullRequest=708) | Remove the redundant footer click interceptor. The actionable header already handles Enter/Space; verdict controls remain native buttons. |
| [client/src/components/events/SubscriptionsTable.tsx:109](https://sonarcloud.io/project/issues?id=Midtown-Technology-Group_bifrost&issues=AaCX0wBRQzi-Va3rw2kp&pullRequest=708) | Use the native label-to-switch activation path and mark the click-propagation wrapper as presentational. Existing tests cover label toggling, pending state, and row isolation. |
| [client/src/components/events/SubscriptionsTable.tsx:113](https://sonarcloud.io/project/issues?id=Midtown-Technology-Group_bifrost&issues=AaCX0wBRQzi-Va3rw2kr&pullRequest=708) | Use the native label-to-switch activation path and mark the click-propagation wrapper as presentational. Existing tests cover label toggling, pending state, and row isolation. |
| [client/src/components/files/FilePolicyEditor.tsx:228](https://sonarcloud.io/project/issues?id=Midtown-Technology-Group_bifrost&issues=AaCX0vd1Qzi-Va3rw2hq&pullRequest=708) | Remove the duplicate conditional branches, preserving their shared value. |
| [client/src/components/files/PoliciesView.tsx:172](https://sonarcloud.io/project/issues?id=Midtown-Technology-Group_bifrost&issues=AaCX0vaaQzi-Va3rw2he&pullRequest=708) | Pass rule and index explicitly to the renderer. Both were intentional inputs; the callback no longer receives the unused array argument. |
| [client/src/components/forms/FormListSurface.tsx:228](https://sonarcloud.io/project/issues?id=Midtown-Technology-Group_bifrost&issues=AaCX0vw8Qzi-Va3rw2jf&pullRequest=708) | Mark the non-interactive propagation wrapper as presentational. The nested switch/button supplies keyboard interaction; the wrapper does not become an extra focus target. |
| [client/src/lib/paginated-query.ts:24](https://sonarcloud.io/project/issues?id=Midtown-Technology-Group_bifrost&issues=AaCX0yGbQzi-Va3rw2wS&pullRequest=708) | Make the existing code-unit ordering explicit. Cache identity must be independent of locale; a regression covers distinct canonically equivalent Unicode keys with reversed insertion order. |
| [client/src/pages/EntityManagement.tsx:1323](https://sonarcloud.io/project/issues?id=Midtown-Technology-Group_bifrost&issues=AaCX0x0iQzi-Va3rw2ug&pullRequest=708) | Supply an explicit comparator for UUID role signatures; comparison remains independent of input ordering. |
| [client/src/pages/ExecutionHistory.tsx:655](https://sonarcloud.io/project/issues?id=Midtown-Technology-Group_bifrost&issues=AaCX0xvwQzi-Va3rw2tx&pullRequest=708) | Remove the duplicate conditional branches, preserving their shared value. |
| [client/src/pages/ExecutionHistory/components/ExecutionCancelAction.tsx:84](https://sonarcloud.io/project/issues?id=Midtown-Technology-Group_bifrost&issues=AaCX0xiYQzi-Va3rw2sA&pullRequest=708) | Mark the non-interactive propagation wrapper as presentational. The nested switch/button supplies keyboard interaction; the wrapper does not become an extra focus target. |
| [client/src/pages/UsageReports.demo.ts:203](https://sonarcloud.io/project/issues?id=Midtown-Technology-Group_bifrost&issues=AaCX0x_RQzi-Va3rw2vZ&pullRequest=708) | False positive: randomness varies synthetic chart counts and durations for the explicitly selected demo view. It creates no credential, authorization decision, or security-sensitive identifier. |
| [client/src/pages/UsageReports.demo.ts:228](https://sonarcloud.io/project/issues?id=Midtown-Technology-Group_bifrost&issues=AaCX0x_RQzi-Va3rw2va&pullRequest=708) | False positive: randomness varies synthetic chart counts and durations for the explicitly selected demo view. It creates no credential, authorization decision, or security-sensitive identifier. |
| [client/src/pages/UsageReports.demo.ts:244](https://sonarcloud.io/project/issues?id=Midtown-Technology-Group_bifrost&issues=AaCX0x_RQzi-Va3rw2vb&pullRequest=708) | False positive: randomness varies synthetic chart counts and durations for the explicitly selected demo view. It creates no credential, authorization decision, or security-sensitive identifier. |
| [client/src/pages/UsageReports.demo.ts:335](https://sonarcloud.io/project/issues?id=Midtown-Technology-Group_bifrost&issues=AaCX0x_RQzi-Va3rw2vf&pullRequest=708) | False positive: randomness varies synthetic chart counts and durations for the explicitly selected demo view. It creates no credential, authorization decision, or security-sensitive identifier. |
| [client/src/lib/chat-utils.ts:10](https://sonarcloud.io/project/issues?id=Midtown-Technology-Group_bifrost&issues=AaCX0yF3Qzi-Va3rw2wP&pullRequest=708) | False positive: the legacy fallback creates local optimistic message IDs only when Web Crypto is unavailable. The ID grants no access; API authentication and server-generated entity IDs remain authoritative. Existing tests cover same-millisecond distinctness. |

The seven security findings above are documented false positives; this record
does not claim their external Sonar issue states were changed. SonarCloud is not
a required main-branch check in the inspected branch protection. Required checks
and all genuine CI failures still gate the merge.

Focused validation: 86 client tests passed across the nine affected test files.
The shell regression passed both mutual exclusion and results-permission cases.
The full exact-commit pre-PR gate remains required after these repairs.


## Comprehensive browser failures

CI run 34724417793 passed every backend shard but found twelve browser failures.
The previous local gate selected only fourteen smoke cases. `pre-pr` now calls
the same comprehensive browser lane as CI; its harness regression and both
agent guides record that boundary. The interrupted `f2600a72` local gate is not
a passing result.

- Agent review now owns its local model profile and observes the durable chat
  completion event before asserting stored review state. Worker logs showed
  cold initialization outlasting the old assertion, followed by teardown
  deleting the still-running record. The test timeout is unchanged.
- Agent navigation, workflow actions, subscription filter cells, table settings,
  and file-policy editing use the current accessible controls. Cancellation
  checks the persisted cancelled outcome and attempts without requiring an
  optional error-message banner. The member-budget test uses its already-owned
  agent instead of an undefined duplicate fixture helper.
- Service MCP discovery includes the required issuer. Private-memory MCP calls
  obtain an audience-bound token through real authorization-code/PKCE flow,
  retaining CSRF headers and JSON/SSE negotiation. Browser session tokens remain
  rejected by MCP. Neither issuer nor audience validation is weakened.
- Both local-origin proxy transports preserve the incoming Host header, and the
  production MCP nginx route preserves its port. The original harness rewrote
  localhost to the internal container name; nginx also stripped the port.
- Roles setup creates a fresh workflow identity on each hook entry. A reused
  Playwright worker reran the hook with a cached module-level name: rewriting
  its deleted source automatically reactivated the workflow, so the subsequent
  register call correctly returned 409. Failed responses retain diagnostics;
  successful browser responses are not read after navigation.
- The event detail artifact exposed duplicated origins in absolute callback
  URLs. URL resolution now handles the API's absolute URLs and relative paths;
  component coverage checks both forms.

Focused runs now cover every formerly failing journey; the last repairs were
verified with CI's two-worker concurrency. The two harness files passed all 18
tests, EventSourceDetail passed all 11 component tests, and lint passed for the
edited browser/component files. The initial new harness test called its path
helper incorrectly; that test defect was corrected before the passing run.
A new exact-commit full gate, including the comprehensive browser suite, remains
required before queueing this PR.
