# Native merge queue throughput recovery

Use this procedure only with authorization to cancel retired CI attempts on
`Midtown-Technology-Group/bifrost`. It does not authorize admission, reruns,
merges, deployment or changes to protection, permissions or queue settings.

## Retire one expired candidate

The operator command accepts only this repository's CI/CodeQL `merge_group`
runs for `main`. It requires an authoritative `checks_timed_out` removal for
the exact candidate SHA, complete queue/history reads and absence from every
current live queue entry. It refuses other removal reasons and completed runs.
An unbuilt `QUEUED` entry legitimately has no combined SHA. Its PR identity must
still be known, and the target PR must not appear anywhere in the live queue,
including requeued/unbuilt entries. Missing heads in other states fail closed.

Run on the supported Linux operator host with the system-managed GitHub CLI at
`/usr/bin/gh`. The command uses that absolute executable path and never searches
`PATH` or falls back to another executable. A missing system CLI stops the run;
do not substitute a user-writable wrapper to obtain recovery evidence.

1. Read the run and queue state. Record the incident/PR owner and purpose.
   Choose one exact run ID, not a branch name or the newest run for a PR.
2. Plan without mutation, using a new private evidence directory:

   ```bash
   node .github/scripts/retire-merge-group.mjs RUN_ID /private/new-plan-directory
   ```

3. Review `plan.json`, `run-before.json` and `live-proof-1.json`. With cancellation
   authorization, apply using another new directory:

   ```bash
   node .github/scripts/retire-merge-group.mjs RUN_ID /private/new-apply-directory --apply
   ```

   The command preserves the complete job inventory and available completed-job
   logs, then refreshes the run identity and live queue before one cancellation
   request. Missing logs or incomplete/failed API responses prevent cancellation.
   No repository source executes with an injected workflow credential; this is
   an operator command using the existing authenticated `gh` session.

4. Inspect `cancel-result.json` and `run-after.json`. `cancellation_requested`
   means accepted submission, not verified completion. Read the original run
   until terminal and retain the terminal metadata and attempt-specific logs:

   ```bash
   gh api repos/Midtown-Technology-Group/bifrost/actions/runs/RUN_ID
   gh api repos/Midtown-Technology-Group/bifrost/actions/runs/RUN_ID/attempts/ATTEMPT/logs > /private/terminal-attempt-logs.zip
   ```

   Bind the terminal metadata and archive to the retained run/attempt/SHA. Active
   job logs cannot always be downloaded before cancellation; GitHub retains them
   after terminal completion. Do not claim those logs were archived beforehand.
   Do not publish raw logs or private receipts in PR comments.

5. Re-read the current queue to verify live candidates remain untouched. Record
   actual cancellation, running/queued job counts and outstanding collection.

An uncertain cancellation response is recorded as `unknown`; do not repeat the
write. Reconcile the original run first. Never reuse an evidence directory or
silently overwrite an earlier mutation receipt. If the candidate reappears or
its attempt changes during preparation, stop. The final read-to-write boundary
is not atomic: keep the operator intervention narrow and stop on any unexpected
queue transition rather than assuming the command removes that race entirely.

## Bounded experiment

Observe a matched preceding 24-hour period, then run this manual procedure for
48 hours and preferably at least twenty admitted candidates. Change only
retired-attempt cancellation. Leave native build concurrency one, ALLGREEN,
single-PR merge limits, the 60-minute response timeout and every gate unchanged.

Baseline, 2026-10-04 through 2026-10-17 UTC: 214 additions, 34 timeout exits.
CI 39163040496 consumed approximately 157.6 of 162.7 runner-minutes after its
candidate was removed at 2026-10-18T21:25:27Z. This one-run waste measurement is
not a fleet average. Measure all sampled attempts, not only successful runs.

Acceptance: at least 50% fewer timeout exits per hundred additions, 80% less
retired-attempt runner work and 25% more successful required-gate completions
per active queue-hour, with comparable admission mix and no increased
non-timeout gate failure rate. Track review capacity, arrivals and concurrent
preparation load. Count verified production delivery separately from merges.

Stop on a live-run cancellation, missing original evidence, identity mismatch
or new delivery ambiguity. Rollback is to stop interventions; no setting or code
activation needs reverting. A cancelled attempt cannot be resumed; a new run
requires its ordinary exact-candidate diagnosis and authorization. Automate
only after evidence supports this experiment and the automation is reviewed.

## Prepare work ahead of the queue

Maintain a small ready buffer rather than refreshing every dormant branch.
Each overlap group needs one owner and one canonical finding/remediation map.
Prioritize repair of shared CI/delivery blockers, then genuine lower stack
layers and oldest qualified independent changes. Main-only PR triggers do not
qualify non-main-base stack layers; inspect their actual producer evidence.

For the initial drain, propose four active platform remediation heads and two
simultaneous full platform qualification runs across participating agents.
These are coordinator WIP budgets, not changes to GitHub protections or a new
merge engine. Existing requirements for current-main clean-candidate pre-PR,
independent review, explicit holds and authorized admission still apply.

Record exact-head review and runtime evidence, original failures and unrun
checks. Retain ambiguous production receipts without replay. A later green
run does not explain an intermittent failure, and a merged infrastructure PR
does not establish deployed policy. Do not count supersession closures, bot
refreshes or repeated CI attempts as independently delivered changes.
