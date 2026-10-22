# MTG merge queue authorization

MTG repository administrators authorize a merge by adding its PR to GitHub's
native merge queue. This applies to their own PRs and workflow changes too.
Upstream collaborators retain write access so they can update the fork branches
used by our upstream PRs.

The required `MTG Queue Authorization` check verifies that the candidate SHA
matches the first live queue entry and that its enqueuer is a human repository
administrator. Missing entries, API errors, bots, and write-only enqueuers fail.
The PR event only satisfies the prerequisite needed to enter the queue; the
merge-group event performs authorization. Keep one PR per group and one build
at a time, with the `MERGE` method to preserve upstream ancestry.

The workflow and policy script execute from the candidate commit, so their
integrity cannot rest on the required check itself. `.github/CODEOWNERS` assigns
the workflow, policy implementation, policy tests, ownership file, and this
plan to `@Midtown-Technology-Group/bifrost-main-maintainers`. Main protection
must require code-owner review, dismiss stale approvals, require approval after
the most recent push, and restrict review dismissal to that team. Thus a write
collaborator cannot change the decision code and self-authorize with the same
check name. Ordinary source changes remain review-optional.

Classic push restrictions and the update-only ruleset cannot remain active:
GitHub's internal merge-queue bot fails their final-write authorization. Rule
suites 4071285434, 4080210837, and 4082314195 record this despite successful CI.
An explicit User exemption for the bot also failed. Retain all existing test
checks, signatures, administrator enforcement, PR/conversation requirements,
force-push/deletion protections, and the mandatory native queue.

Install the new check bound to GitHub Actions before disabling the incompatible
update rule. Verify its PR prerequisite, then enqueue as an MTG administrator.
Confirm live merge-group authorization and the final merge before promotion
through `bifrost-infra`. Read back branch protection after any rules change;
`require_code_owner_reviews`, `dismiss_stale_reviews`, and
`require_last_push_approval` must be true, and dismissal restrictions must name
the `bifrost-main-maintainers` team.

Policy tests run with `node --test .github/scripts/authorize-merge-queue.test.mjs`
in the workflow and `./test.sh pre-pr`. They cover authorized administrators,
write-only denial, identity mismatch, bots, stale/non-first entries, malformed
events, API errors, and the PR prerequisite.
