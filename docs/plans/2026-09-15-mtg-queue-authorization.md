# MTG merge queue authorization

The fork keeps upstream collaborators' write access so they can update branches
used by our pull requests to upstream. Only an MTG repository administrator may
authorize a merge into the fork's `main` by adding a PR to GitHub's merge queue.

GitHub's classic push restriction rejects the internal `github-merge-queue[bot]`
when it writes the final merge commit. Rule suites 4071285434 and 4080210837
record this authorization failure after all CI, signature, review, and queue
checks passed. The internal bot cannot be resolved as an eligible push-allowlist
actor. Disabling classic administrator enforcement did not fix this.

## Enforcement

- Require `MTG Queue Authorization`, bound to the GitHub Actions app, alongside
  the existing required checks. Before queue admission the check passes its
  prerequisite. On `merge_group`, it verifies the exact candidate SHA is the
  first live queue entry and its enqueuer is a human repository administrator.
  API errors, missing entries, bots, and write-only collaborators fail the gate.
- Keep the queue configured for one PR per merge group and one concurrent build.
  Its merge method remains `MERGE` to preserve upstream ancestry.
- Require one review from `bifrost-main-maintainers` for changes to `.github/**`.
  This covers the gate script, its tests, all workflows, and any new workflow
  that could impersonate the required check name. No actors bypass this rule.
- Keep workflow tokens read-only unless a specific job needs a documented write
  permission. No workflow executing proposed repository code may receive
  `checks: write` or `statuses: write`; otherwise it could forge this gate.
- Keep all existing checks, signatures, PR/conversation requirements, deletion
  and force-push protections, and the native merge queue mandatory without bypass.
- Only after these controls are active, remove the incompatible classic push
  allowlist. Do not change collaborator permissions.

The administrator grant is the authorization boundary. MTG owns those grants.
An administrator's queue admission authorizes normal changes without another
review. Changes to `.github/**` additionally require another MTG reviewer.

## Installation and verification

1. Keep the existing main push restriction while reviewing this change.
2. Enable the scoped `.github/**` review rule and require the new app-bound check.
3. Obtain an MTG review of this exact gate installation. The PR author cannot
   approve their own PR, and queue admission does not bypass required team reviews.
4. Confirm required PR checks and review, then remove the classic push allowlist
   and enqueue as an MTG administrator. Leave every other protection active.
5. Verify the live authorization job succeeds with its repository-scoped token,
   all queue checks pass, and GitHub merges through the queue with both parents.
6. Promote the published image digests and deploy through `bifrost-infra`.

The policy tests run with
`node --test .github/scripts/authorize-merge-queue.test.mjs`, in the authorization
workflow and in `./test.sh pre-pr`. They cover administrator admission, write-only
denial, identity mismatch, bots, stale/removed/non-first queue entries, API errors,
and the prerequisite check needed before a queue entry exists.

Installation is incomplete until the live queue check and final merge succeed.
