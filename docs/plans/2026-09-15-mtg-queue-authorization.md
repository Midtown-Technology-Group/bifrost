# MTG merge queue authorization

MTG repository administrators authorize a merge by adding its PR to GitHub's
native merge queue. This applies to their own PRs and workflow changes too.
No second-maintainer review is required. Upstream collaborators retain write
access so they can update the fork branches used by our upstream PRs.

The required `MTG Queue Authorization` check verifies that the candidate SHA
matches the first live queue entry and that its enqueuer is a human repository
administrator. Missing entries, API errors, bots, and write-only enqueuers fail.
The PR event only satisfies the prerequisite needed to enter the queue; the
merge-group event performs authorization. Keep one PR per group and one build
at a time, with the `MERGE` method to preserve upstream ancestry.

This uses the repository's existing CI trust model. A collaborator who can
rewrite workflows can alter this guard, just as they can alter the other CI
checks. It prevents unauthorized queue admission from producing a successful
merge under the reviewed workflow; it is not isolation from malicious workflow
authors. No additional workflow-review requirement is introduced.

Classic push restrictions and the update-only ruleset cannot remain active:
GitHub's internal merge-queue bot fails their final-write authorization. Rule
suites 4071285434, 4080210837, and 4082314195 record this despite successful CI.
An explicit User exemption for the bot also failed. Retain all existing test
checks, signatures, administrator enforcement, PR/conversation requirements,
force-push/deletion protections, and the mandatory native queue.

Install the new check bound to GitHub Actions before disabling the incompatible
update rule. Verify its PR prerequisite, then enqueue as an MTG administrator.
Confirm live merge-group authorization and the final merge before promotion
through `bifrost-infra`.

Policy tests run with `node --test .github/scripts/authorize-merge-queue.test.mjs`
in the workflow and `./test.sh pre-pr`. They cover authorized administrators,
write-only denial, identity mismatch, bots, stale/non-first entries, malformed
events, API errors, and the PR prerequisite.
