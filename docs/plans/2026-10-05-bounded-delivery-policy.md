# Risk-bounded delivery: reviewed activation plan

Status: **draft, shadow only**. This change does not replace a current gate.
Lane 3 applies to this PR because it changes release-control code. It requires
human review under the existing delivery policy and normal administrator queue
admission. Do not approve or merge this control change using its own evaluator.

## Outcome

An authorized request to deliver a routine compatible change should cover its
fix/test/review/queue/publish/promote/verify loop. People should decide risk
exceptions and the trust policy, rather than approve each mechanically revised
routine head. Every new head still needs fresh machine evidence. No labels,
chat claim, model self-review, admin bypass or green badge grants authority.

The three decision surfaces are deliberately separate:

1. **Source merge:** classify the exact source diff under policy from protected
   main; independent completed review; signed contributors; current required CI,
   security and candidate-image evidence; native merge queue's combined tree.
2. **Release publication:** verify the exact reviewed release interval, generated
   metadata/manifests, version/contract floor, signed merge and successful main
   CI; publish immutable packages and signed provenance, never move a tag.
3. **Production promotion:** verify signed release manifest and exact API/client/
   worker digest tuple; reviewed infra pin; compatible schema/CLI; supported
   rollback; same-source preview; generation-fenced admission/drain and accepted
   work; protected apply; independent production route, execution and reopen
   readback. Source CI cannot establish this evidence.

## Executable first slice

`.github/scripts/bounded-delivery-policy.mjs` is a read-only collector and
fail-closed evaluator. `.github/workflows/bounded-delivery-policy.yml` runs its
adversarial tests and reports shadow eligibility from **protected-main code**.
It never checks out the candidate in the privileged event job, installs its
packages, writes a review, adds a label, enqueues, merges, tags or deploys. Its
permissions are read-only. An error exits unsuccessfully and never means allow.

The proposed initial source scope is seven explicitly named presentation
primitives. All unknown paths, deletions, renames, symlinks and executable modes
require a human. Control files take precedence over routine paths. Changes to
API/CLI/schema/contracts, migrations, authentication, tenant isolation,
credentials, network, permissions, execution, delivery, dependency/container
inputs, or deployment are outside this allowlist. Expanding it is a reviewed
control-policy change, never a PR-provided classification. A reviewer must also
attest no exceptional semantic risk: a path alone does not prove compatibility.

The shipped reviewer allowlist is empty because no qualified independent
attestation producer has been established. This is an explicit activation
blocker, not a claim that the current reviewer integration is broken.
`mode` accepts only `shadow`; `authorized` is always false. Even a synthetic
fully eligible case reports release and deployment as `human-required`.

### Evidence contract

All evidence is fetched directly from GitHub for the exact repository, PR,
protected-main policy commit and candidate SHA. There is no local evidence-file
input. The collector checks a complete bounded file/commit/check inventory,
regular Git blob modes, signed commits, resolved author/committer identities,
latest decisive reviews by submission time, provider IDs, current check-suite state
and workflow ID/path/run provenance. Unresolved coauthor trailers require a human. Newer failed
or pending checks invalidate older successes. Mutable review/check/suite evidence and candidate/main identities are
read back after collection. Reports are observations, never reusable tokens.

Independent review requires both:

- A current, nondismissed formal `APPROVED` GitHub review from an explicitly
  pinned bot user, different from the PR author and every commit author or
  committer. A human account used by the authoring assistant cannot substitute.
- A successful GitHub Check Run from its separately pinned App identity,
  bound to this exact PR/head/base, whose plain JSON `output.summary` is the
  following authenticated provider attestation. `external_id` must equal
  `sha256:` plus SHA-256 of the canonical JSON object below. GitHub's authenticated
  App attribution is the trust boundary; the hash binds content and is not a
  replacement for a provider signature or independent review.

```json
{
  "schema": "mtg.independent-review/v1",
  "repository": "Midtown-Technology-Group/bifrost",
  "pullRequest": 12,
  "headCommit": "<exact 40-character head SHA>",
  "policyCommit": "<exact protected-main SHA>",
  "reviewId": 50,
  "inventorySha256": "<digest of every changed filename, status and blob SHA>",
  "reviewed": "complete",
  "outcome": "approved",
  "override": false,
  "risks": []
}
```

Canonical field order is the example order and the evaluator's `expected`
object. The inventory uses `inventoryDigest`: UTF-8 JSON of the array of
`{filename,status,sha}` objects sorted by filename using code-point order.
Unknown fields, incomplete/excluded scope, human overrides, stale/replayed
heads, altered inventory, missing data and nonempty risk flags fail closed.
Do not mint this attestation from an author's text, a model's own claim, an
`APPROVED` override or an unqualified green provider status.

Checks are allowlisted by numeric App ID and slug. GitHub Actions results also
require the exact workflow path, event, repository, PR/head/base and completed
successful run. The policy does not replace branch protection's full required
check set or native merge-group checks. No skipped check is treated as success.

## Existing reviewer qualification, 2026-10-05

The installed CodeRabbit bot has verified GitHub user ID `136622811`, node ID
`BOT_kgDOCCSy2w`, login `coderabbitai[bot]`, type `Bot`. The avatar's numeric App
path is not accepted as verified Check Run App identity.

- [PR #1058 exact-head statuses](https://api.github.com/repos/Midtown-Technology-Group/bifrost/commits/36cb0cd72d66cd71a0101c8f956d8677f34bcf00/statuses?per_page=100)
  include a successful CodeRabbit status whose description is **Review rate
  limited**. Its substantive review is `COMMENTED` on an earlier head.
- [PR #1057 review](https://github.com/Midtown-Technology-Group/bifrost/pull/1057#pullrequestreview-5404360177)
  remains `COMMENTED` despite a completed-review legacy status.
- The recent 14-nondraft-PR sample had no CodeRabbit `APPROVED` review. The
  inspected current check-run sets had no CodeRabbit App check.
- [CodeRabbit's official approval workflow](https://docs.coderabbit.ai/pr-reviews/request-changes-workflow)
  can issue approval through override commands or fully excluded file scopes.
  Formal approval alone therefore does not establish complete independent
  coverage. Enabling that setting alone is not this policy's activation.

No reviewer configuration, GitHub App grants, credentials, protection settings
or production environment permissions were changed for this investigation.
The GitHub integration could read the active native-queue ruleset but could
not read classic branch protection (HTTP 403). Do not infer its complete
required-check set or use another route to evade that access restriction.

## Ordered activation acceptance criteria

These are the remaining concrete blocking slices, not permission to execute
settings changes. Keep each control change reviewed under the previous policy.

1. **Qualify the existing reviewer or an independent adapter.** Choose its
   immutable bot/App identities with the maintainer. Demonstrate real positive
   and negative fixtures for full coverage, exceptions, changed heads,
   rate-limit/skip, overrides, app forgery and replay. The adapter must fetch
   verifiable provider completion/coverage/override provenance; if that data is
   unavailable, leave human review in place. Do not solve this by trusting the
   authoring assistant's shared GitHub identity. Any new App grant, credential
   or persistent permission expansion needs action-time approval.
2. **Activate bounded source admission in a separate reviewed PR.** Pin that
   producer in protected policy and wire the evaluator into the existing
   native-queue authorization seam, preserving administrator-authorized human
   fallback for exceptional paths and exact combined-tree checks. Revalidate
   the live head/base/evidence immediately before enqueue and at the first live
   queue entry. Restrict any future enqueuer to its explicit App identity;
   never accept any bot, caller label or arbitrary admin override. Obtain the
   separately required approval before changing GitHub security settings.
3. **Replace only the release review decision.** In
   `scripts/release/automatic-release.py:checked_release`, retain signed commit,
   exact main-run, complete PR interval, release-only diff and candidate checks.
   A routine release must prove all included source PRs eligible under the
   then-current trusted policy and verify generated metadata against the
   trusted generator. Breaking contract floors or any exception retain the
   human exact-head decision. Do not treat the five release metadata paths as
   sufficient source-risk classification. Preserve #1062's manifest/notes work.
4. **Consume release evidence in bifrost-infra.** Retain `platform_release.py`
   attestation verification, immutable image locks and the protected App
   Service lane. Before allowing routine promotion, add direct collectors for
   same-source preview fingerprint, current schema/CLI compatibility, rollback
   pin, admission/drain generation and accepted-work counts. Apply is bounded
   to the existing production target; migrations, network, auth, grants and
   resource changes remain exceptions. A signed manifest is necessary and
   cannot replace live compatibility/drain evidence.
5. **Prove completion and recovery.** Exercise a canary and a rollback-compatible
   release using the normal protected workflows. Require independent route,
   runtime version/digest, execution and reopened-admission readback. Test
   interruption after accepted mutation: read back the receipt and exact live
   state before retry. Production verification failures stop promotion and
   use only the separately authorized compatible recovery path.

The implementation deliberately refuses to activate through an environment
variable or a mode flip. Activation requires a reviewed wiring change as well
as qualified producer evidence. Until then, all existing Lane 3 human review,
release exact-head approval, administrator queue and infrastructure gates stay
in force. The initial source allowlist does not claim to cover normal backend
or release changes yet.

## Verification

`node --test .github/scripts/bounded-delivery-policy.test.mjs` exercises the
collector's API seam, not an alternate fake production decision path. The
branch-push workflow also runs the literal clean-candidate `bash test.sh pre-pr`
before the draft PR is opened; ordinary required PR CI remains mandatory.
The shadow workflow itself can run only after its protected-main definition is
reviewed and merged. Its successful tests are not a successful live review,
release publication, promotion or production-verification claim.
