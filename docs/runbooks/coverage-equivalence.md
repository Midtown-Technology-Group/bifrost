# Compare parallel and serial Python coverage

Use this bounded experiment to determine whether ordinary CI test shards can
produce the Python evidence currently collected by the serial Sonar lane.
The existing collector, Sonar analysis, security findings, quality thresholds,
reviews, queue admission and production holds remain authoritative. This
experiment neither submits a second Sonar analysis nor removes a test lane.

## Scope and hypothesis

On an isolated non-main branch, manually run ordinary comprehensive CI, then
instrument its existing fast-unit and four E2E shards on the **same commit**.
A supplement runs the slow units and repository Python tools unique to the
serial collector. The serial collector runs alongside these producers.
Client Vitest and repository Node coverage remain in the authoritative lane.

Hypothesis: the parallel union preserves every baseline test outcome, authored
Python file, source line, branch denominator and observed branch arc while
reducing the eventual critical path by at least 20 minutes and runner demand by
at least 25 minutes. These are acceptance targets, not measured savings.

The three sampled runs below spent 42.72–46.95 minutes in comprehensive Python
collection, with 35.77–47.67 minutes between ordinary test completion and Sonar
completion. Analysis itself took only 2.42–2.83 minutes:

- [39179296064](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39179296064)
- [39178071667](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39178071667)
- [39179259678](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39179259678)

An initial estimate is 25–40 runner-minutes and 20–35 wall minutes saved per
eligible comprehensive run after a later approved cutover. Supplement cost,
instrumentation overhead, runner contention and the remaining client/Node work
can reduce or eliminate that saving. PR arrival and deployment capacity are
separate constraints; this does not predict delivered changes per day.

## Run one pair

Use normal clean-candidate signing and pre-PR procedures. Do not change the
branch between observations. Record the full SHA and both run IDs/attempts.
First dispatch a control with the default inputs:

```bash
gh workflow run ci.yml --repo Midtown-Technology-Group/bifrost \
  --ref t3/coverage-equivalence-shadow -f coverage_shadow=false
```

After that exact-head run succeeds, dispatch **one** pilot:

```bash
gh workflow run ci.yml --repo Midtown-Technology-Group/bifrost \
  --ref t3/coverage-equivalence-shadow \
  -f coverage_shadow=true -f coverage_shadow_control_run=CONTROL_RUN_ID
```

The control supplies timing only; none of its coverage enters the union. A
manual run forces the existing comprehensive test plan. PR, merge-group and
main events cannot activate the experiment. Do not enable it on main.
Before accepting timing estimates, archive and compare the API image manifest
digests printed by the completed control and pilot pull steps for the unit and
four E2E jobs. Control images are not automatically bound by a shadow receipt;
missing or different digests invalidate timing acceptance. Within the pilot,
serial/shard image-ID equality is checked automatically.
Inspect the first pair before authorizing additional observations. A source
repair invalidates the pair: obtain a new successful control on the repaired
revision. Preserve failed attempts; do not retry unexplained failures to green.

## Evidence and equivalence

The pilot retains seven producer artifacts for 14 days, named with run ID,
attempt and lane. Each manifest binds commit, Git tree, workflow source,
coverage/configuration hashes and test-image ID. The comparison requires the
complete successful provider job inventory for that same attempt and rejects
missing, duplicate, stale, mixed, extra, corrupt or incompatible inputs.

Each producer snapshots raw runtime databases, test-runner data, container
identities and phase JUnit before coverage validation and stack cleanup.
Failed snapshots remain diagnostics and cannot qualify as a sealed artifact.
Existing service-log export still runs at teardown. Evidence may contain
internal source paths and test parameters; retain it under repository access
controls and do not publish raw diagnostics in comments.

The union uses Coverage.py with the existing path mappings and reporting
configuration. It excludes the serial baseline from its inputs. Validation
requires exact test IDs and passed/skipped dispositions, compatible branch
databases, identical authored/zero-hit file inventories, no lost baseline
arcs, and identical source-line/branch denominators. Additional arcs are
reported for inspection. Missing evidence remains a failure.

Runtime attribution checks API and replica reports in all lanes, plus worker,
scheduler and scheduler-fixtures in each E2E lane and the baseline. Fast and
slow units retain existing worker/scheduler isolation. These are parent-process
receipts: they do **not** establish coverage of every spawned interpreter or
fork. A baseline blind spot remains a blind spot; any unique subprocess
instrumentation needs separate investigation and approval.

The comparison does not validate client/Node equivalence or replace their
existing evidence. Empty runtime exports and Sonar-uncredited new-code coverage
are independent defects. Preserve and diagnose the actual producer report and
scanner import evidence separately. Do not change exclusions or security
findings to make this experiment pass.

## Acceptance and stop criteria

`comparison.json` with `status: equivalent` establishes that pair's offline
Python data comparison only. It does **not** authorize rollout or merge.
Before proposing removal of redundant collection, require:

- Five successful paired observations within 48 hours, each on an exact shared
  revision and image, with no missing producers or unexplained retries.
- Exact test execution and denominator equivalence, no lost baseline arcs,
  and an explanation for any additional arcs or nondeterministic coverage.
- At least 25 runner-minutes and 20 wall minutes of estimated savings per pair,
  after supplement, export, combine and setup costs. Reconcile final comparison
  cleanup/upload timing from completed job metadata before accepting estimates.
- Aggregate ordinary-test instrumentation overhead at most 20%, with no single
  shard above 30%, compared with its same-revision control. Inspect absolute
  timings, runner wait and cache/contention differences as well as ratios.
- No test/assertion/selection/isolation changes, no degraded diagnostics,
  and no change to authoritative Sonar qualification or production safeguards.

Stop after the first failed comparison, runtime export, source/image mismatch,
unexplained test failure, overhead limit breach, or evidence-retention failure.
Archive evidence, identify the cause, and obtain a disposition before another
observation. An aggregate green check does not waive any of these conditions.

The pilot deliberately executes both paths and therefore **adds** runner cost.
Its report models removal of only serial Python execution while retaining
client/Node collection and all existing gates. Counterfactual wall savings use
actual producer completion and unaffected-job bounds; runner supply and review
or deployment serialization may still limit throughput.

## Rollback and next decision

Set `coverage_shadow=false` (the default) and stop pilot dispatches. No persistent
setting, required check or production state needs restoration. If workflow
reversion is needed, revert the experiment commit through ordinary reviewed CI.
Do not remove the serial collector until equivalence is demonstrated, reviewed,
and implementation is explicitly authorized. A later cutover must separately
prove PR merge-tree, native merge-group and main behavior; this manual pilot
alone cannot prove those event paths.
