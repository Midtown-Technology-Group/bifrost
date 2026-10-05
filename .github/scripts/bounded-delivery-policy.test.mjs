import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { classify, evaluate, inventoryDigest } from "./bounded-delivery-policy.mjs";

const repository = "Midtown-Technology-Group/bifrost";
const head = "a".repeat(40);
const base = "b".repeat(40);
const blob = "c".repeat(40);
const root = `/repos/${repository}`;
const policy = JSON.parse(await readFile(new URL("../bounded-delivery-policy.json", import.meta.url)));

function fixture() {
  const state = {
    policy: { ...structuredClone(policy),
      reviewers: [{ userId: 42, login: "independent[bot]", appId: 500, appSlug: "independent", checkName: "Independent review" }],
      requiredChecks: [policy.requiredChecks[0], policy.requiredChecks.at(-2)],
    },
    main: { object: { sha: base } },
    pr: { number: 12, state: "open", draft: false, user: { id: 1 }, changed_files: 1, commits: 1,
      head: { sha: head, repo: { id: 1187656209, full_name: repository } },
      base: { ref: "main", sha: base, repo: { id: 1187656209, full_name: repository } } },
    files: [{ filename: "client/src/components/ui/button.tsx", status: "modified", sha: blob }],
    tree: { sha: "d".repeat(40), truncated: false, tree: [{ path: "client/src/components/ui/button.tsx", sha: blob, type: "blob", mode: "100644" }] },
    commits: [{ sha: head, author: { id: 1 }, committer: { id: 2 }, commit: { verification: { verified: true, reason: "valid" } } }],
    reviews: [{ id: 50, user: { id: 42, login: "independent[bot]", type: "Bot" }, state: "APPROVED", commit_id: head, submitted_at: "2026-10-05T10:00:00Z",
      html_url: `https://github.com/${repository}/pull/12#pullrequestreview-50` }],
    checks: [],
    workflow: { id: 77, path: ".github/workflows/ci.yml" },
    run: { id: 99, workflow_id: 77, check_suite_id: 152, head_sha: head, path: ".github/workflows/ci.yml", repository: { full_name: repository },
      head_repository: { full_name: repository }, event: "pull_request", status: "completed", conclusion: "success",
      pull_requests: [{ number: 12, head: { sha: head }, base: { sha: base } }] },
    calls: [],
  };
  state.attestation = { schema: "mtg.independent-review/v1", repository, pullRequest: 12,
    headCommit: head, policyCommit: base, reviewId: 50, inventorySha256: inventoryDigest(state.files),
    reviewed: "complete", outcome: "approved", override: false, risks: [] };
  const check = (id, name, appId, appSlug) => ({ id, name, app: { id: appId, slug: appSlug },
    head_sha: head, status: "completed", conclusion: "success", check_suite: { id: id + 100 } });
  state.checks = [
    { ...check(51, "Independent review", 500, "independent"),
      pull_requests: [{ number: 12, head: { sha: head }, base: { sha: base, repo: { id: 1187656209 } } }],
      output: { summary: JSON.stringify(state.attestation) },
      external_id: "sha256:" + createHash("sha256").update(JSON.stringify(state.attestation)).digest("hex") },
    { ...check(52, "Plan Affected Tests", 15368, "github-actions"),
      details_url: `https://github.com/${repository}/actions/runs/99/job/52` },
    check(53, "CodeQL", 57789, "github-advanced-security"),
  ];
  state.suites = new Map(state.checks.map((c) => [c.check_suite.id, { id: c.check_suite.id, app: c.app, head_sha: head, status: "completed", conclusion: "success" }]));
  let reads = 0; let reviewReads = 0;
  state.input = { policy: state.policy, policySha: base, prNumber: 12, request: async (path) => {
    state.calls.push(path);
    if (path === `${root}/git/ref/heads/main`) return state.main;
    if (path === `${root}/pulls/12`) return ++reads > 1 && state.recheck ? state.recheck : state.pr;
    if (path === `${root}/git/trees/${head}?recursive=1`) return state.tree;
    if (path.includes("/files?")) return state.files;
    if (path.includes("/commits?")) return state.commits;
    if (path.includes("/reviews?")) return ++reviewReads > 1 && state.refreshedReviews ? state.refreshedReviews : state.reviews;
    if (path.includes("/check-suites?")) return { total_count: state.suites.size, check_suites: [...state.suites.values()] };
    if (path.includes("/check-runs?")) return { total_count: state.checks.length, check_runs: state.checks };
    if (path === `${root}/actions/runs/99`) return state.run;
    if (path === `${root}/actions/workflows/77`) return state.workflow;
    if (path.includes("/check-suites/")) return state.suites.get(Number(path.split("/").at(-1)));
    throw new Error(`Unexpected evidence request: ${path}`);
  } };
  return state;
}

test("authentic independent evidence yields shadow eligibility, never authority", async () => {
  const state = fixture();
  const result = await evaluate(state.input);
  assert.equal(result.source, "eligible-after-reviewed-activation");
  assert.equal(result.authorized, false);
  assert.equal(result.release, "human-required");
  assert.equal(result.deployment, "human-required");
  assert.equal(result.policyCommit, base);
  assert.equal(result.headCommit, head);
});

test("shipped policy is shadow-only and trusts no unapproved reviewer", async () => {
  assert.equal(policy.mode, "shadow");
  assert.deepEqual(policy.reviewers, []);
  const state = fixture();
  state.input.policy = policy;
  await assert.rejects(evaluate(state.input), /independent exact-head/);
});

for (const path of [".github/bounded-delivery-policy.json", ".github/workflows/ci.yml", "scripts/release/automatic-release.py",
  "api/alembic/versions/migration.py", "api/src/core/auth.py", "config/network.json", "client/src/lib/permissions.ts",
  "docs/dev/delivery-lanes.md", "AGENTS.md", "client/src/components/ui/AGENTS.md", "unknown.py", "release/candidate.json"]) {
  test(`human decision for ${path}`, async () => {
    const state = fixture();
    state.files[0].filename = path;
    const result = await evaluate(state.input);
    assert.equal(result.source, "human-required");
    assert.equal(result.authorized, false);
    assert.equal(state.calls.some((call) => call.includes("/reviews")), false);
  });
}

for (const [name, mutate] of [
  ["unsupported policy", s => { s.policy.mode = "enabled"; }],
  ["policy from PR head", s => { s.input.policySha = head; }],
  ["stale protected base", s => { s.main.object.sha = head; }],
  ["fork source", s => { s.pr.head.repo.full_name = "attacker/bifrost"; }],
  ["draft PR", s => { s.pr.draft = true; }],
  ["missing file", s => { s.pr.changed_files = 2; }],
  ["duplicate path", s => { s.files.push(s.files[0]); s.pr.changed_files = 2; }],
  ["traversal path", s => { s.files[0].filename = "client/../AGENTS.md"; }],
  ["noncanonical separator", s => { s.files[0].filename = "client\\src\\components\\ui\\button.tsx"; }],
  ["unknown file status", s => { s.files[0].status = "copied"; }],
  ["symlink", s => { s.tree.tree[0].mode = "120000"; }],
  ["executable source", s => { s.tree.tree[0].mode = "100755"; }],
  ["wrong source blob", s => { s.tree.tree[0].sha = base; }],
  ["truncated source tree", s => { s.tree.truncated = true; }],
  ["unsigned source", s => { s.commits[0].commit.verification.verified = false; }],
  ["unknown commit author", s => { s.commits[0].author = null; }],
  ["self approval", s => { s.pr.user.id = 42; }],
  ["coauthor approval", s => { s.commits[0].author.id = 42; }],
  ["human account relabeled as agent", s => { s.reviews[0].user.type = "User"; }],
  ["lookalike reviewer login", s => { s.reviews[0].user.id = 43; }],
  ["stale review", s => { s.reviews[0].commit_id = base; }],
  ["comment only", s => { s.reviews[0].state = "COMMENTED"; }],
  ["dismissed review", s => { s.reviews.push({ ...s.reviews[0], id: 51, state: "DISMISSED" }); }],
  ["requested changes", s => { s.reviews.push({ ...s.reviews[0], id: 51, user: { id: 3 }, state: "CHANGES_REQUESTED" }); }],
  ["foreign review provenance", s => { s.reviews[0].html_url = "https://example.com/review"; }],
  ["forged app identity", s => { s.checks[0].app.id = 501; }],
  ["skipped reviewer", s => { s.checks[0].conclusion = "skipped"; }],
  ["rate-limited reviewer", s => { s.checks[0].output.summary = "Review rate limited"; }],
  ["missing attestation", s => { s.checks[0].output.summary = "null"; }],
  ["tampered attestation digest", s => { s.checks[0].external_id = "sha256:" + "0".repeat(64); }],
  ["approval override", s => { s.attestation.override = true; s.checks[0].output.summary = JSON.stringify(s.attestation); }],
  ["partial coverage", s => { s.attestation.reviewed = "partial"; s.checks[0].output.summary = JSON.stringify(s.attestation); }],
  ["replayed attestation", s => { s.attestation.headCommit = base; s.checks[0].output.summary = JSON.stringify(s.attestation); }],
  ["wrong file inventory", s => { s.attestation.inventorySha256 = "f".repeat(64); s.checks[0].output.summary = JSON.stringify(s.attestation); }],
  ["unknown risk classification", s => { s.attestation.risks = ["unknown"]; s.checks[0].output.summary = JSON.stringify(s.attestation); }],
  ["attestation from another PR", s => { s.checks[0].pull_requests[0].number = 13; }],
  ["rerequested independent review", s => { s.suites.get(151).status = "queued"; s.suites.get(151).conclusion = null; }],
  ["rerequested security scan", s => { s.suites.get(153).status = "queued"; s.suites.get(153).conclusion = null; }],
  ["wrong suite identity", s => { s.suites.get(153).head_sha = base; }],
  ["malformed check ID", s => { s.checks.push({ ...s.checks[2], id: "broken", conclusion: "failure" }); }],
  ["unknown review state", s => { s.reviews.push({ ...s.reviews[0], id: 51, state: "UNKNOWN" }); }],
  ["missing repository ID", s => { delete s.pr.base.repo.id; delete s.checks[0].pull_requests[0].base.repo.id; }],
  ["unresolved coauthor", s => { s.commits[0].commit.message = "fix\n\nCo-authored-by: reviewer <review@example.com>"; }],
  ["later submitted lower-ID rejection", s => { s.reviews.push({ ...s.reviews[0], id: 49, state: "CHANGES_REQUESTED", submitted_at: "2026-10-05T11:00:00Z" }); }],
  ["review changes during collection", s => { s.refreshedReviews = [...s.reviews, { ...s.reviews[0], id: 51, state: "CHANGES_REQUESTED" }]; }],
  ["new queued suite before check creation", s => { s.suites.set(160, { id: 160, app: s.checks[0].app, head_sha: head, status: "queued", conclusion: null }); }],
  ["missing security evidence", s => { s.checks.pop(); }],
  ["newer failed scan", s => { s.checks.push({ ...s.checks[2], id: 60, conclusion: "failure" }); }],
  ["newer pending scan", s => { s.checks.push({ ...s.checks[2], id: 60, status: "in_progress", conclusion: null }); }],
  ["same-name forged check", s => { s.checks[1].app.id = 400; }],
  ["wrong workflow", s => { s.workflow.path = ".github/workflows/attacker.yml"; }],
  ["wrong run suite", s => { s.run.check_suite_id = 999; }],
  ["wrong workflow event", s => { s.run.event = "workflow_dispatch"; }],
  ["unrelated workflow run", s => { s.run.pull_requests[0].number = 13; }],
  ["failed workflow", s => { s.run.conclusion = "failure"; }],
  ["changed head during collection", s => { s.recheck = structuredClone(s.pr); s.recheck.head.sha = base; }],
  ["changed base during collection", s => { s.recheck = structuredClone(s.pr); s.recheck.base.sha = head; }],
  ["unavailable evidence", s => { s.input.request = async () => { throw new Error("HTTP 403"); }; }],
]) {
  test(`fails closed: ${name}`, async () => {
    const state = fixture(); mutate(state);
    await assert.rejects(evaluate(state.input));
  });
}

test("rename out of a control path cannot become routine", () => {
  const result = classify([{ filename: "client/src/components/ui/button.tsx", status: "renamed", previous_filename: ".github/scripts/authorize-merge-queue.mjs" }], policy);
  assert.equal(result.risk, "human-required");
  assert.ok(result.reasons.includes("human-controlled-path"));
});

test("a blanket allowlist still cannot authorize the policy itself", () => {
  const broad = { ...policy, routinePaths: [".github/", "client/"] };
  assert.equal(classify([{ filename: ".github/bounded-delivery-policy.json", status: "modified" }], broad).risk, "human-required");
});

test("workflow path may be ref-qualified; authenticated workflow identity owns its path", async () => {
  const state = fixture(); state.run.path += "@main";
  assert.equal((await evaluate(state.input)).source, "eligible-after-reviewed-activation");
});
