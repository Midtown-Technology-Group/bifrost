# Upstream follow-up through 88c4627bd

Upstream added three commits while the preceding integration batch (#709)
completed verification. Each fix was already present in the Midtown fork:

| Upstream commit | Existing fork commit | Behavior |
| --- | --- | --- |
| `edcf94b33` (#739) | `572fccd3c` (#473) | Resolve the legacy Indianapolis timezone to its canonical zone. |
| `21bc39aab` (#740) | `de9aaf40b` (#514) | Queue the event created by the current webhook request. |
| `7764ccb7a` (#741) | `b9e206ad1` (#487) | Lock rotating OAuth tokens through refresh and persistence. |

A normal two-parent merge, `6d9e4774084d8f1c57712ed429db91d8a5a2f5f0`,
records upstream ancestry. Its tree equals its first parent's tree
(`5c8f673996175e2dd3c77c6ea71ea1d2408f1a59`). No merge strategy discarded
upstream changes: the three conflicts were reviewed individually. The scheduler
conflict was whitespace. The CLI test retains the fork's credential-redacted
response and row-lock assertions. The event test retains the existing persisted
ID regression and broader delivery coverage, without restoring the removed
`filter_expression` field.

The preceding candidate `c495bfacb67e29811b77557b38bdfd516738121a` passed
`./test.sh pre-pr`: 3,052 client tests, 10,013 backend unit tests (3 skipped),
1,890 backend E2E tests (40 skipped), 159 browser tests, lint/type checks, and
the production API runtime check. This follow-up still requires its own
clean-commit gate against the actual merged main before publication and queueing.

That gate passed for `0c26ce3e1040651fdb255b6204df93d1eb9db286` with the
same test counts. During validation, upstream added `88c4627bd` (#743).
The original browser-active artifact hardening was already in the fork; the
additional changes force attachment/octet-stream response metadata on presigned
SDK and MCP download URLs, including URLs for previously stored artifacts.
Safe artifact metadata remains unchanged. The merge retains the upstream unit,
live SDK, and MCP regression coverage and its scheduler leadership test-isolation
repair. Both conflicts were additive test changes and were retained.

The final candidate requires focused artifact/scheduler verification and its own
clean-commit `./test.sh pre-pr` gate before publication and queueing.

PR #710 review found that the shared download service also forwards response
overrides to Midtown's Azure adapter. Its download method must accept those
options and sign the corresponding SAS response headers. The repair covers both
account-key and user-delegation signing, including the no-override call path,
with real SDK signing behind the shared service boundary.
