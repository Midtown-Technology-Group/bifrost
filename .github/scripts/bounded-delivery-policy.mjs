// Read-only policy evaluation. This module has no merge, approval, or deploy API.
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const REPOSITORY = "Midtown-Technology-Group/bifrost";
const SHA = /^[0-9a-f]{40}$/;
const integer = (n) => Number.isSafeInteger(n) && n > 0;
const fail = (message) => { throw new Error(message); };

function canonicalPath(path) {
  return typeof path === "string" && path.length > 0 &&
    !path.startsWith("/") && !/[\\\x00-\x1f\x7f]/.test(path) &&
    path.split("/").every((part) => part && part !== "." && part !== "..");
}

function validatePolicy(policy) {
  if (policy?.schema !== 1 || policy.repository !== REPOSITORY || policy.mode !== "shadow" ||
      !Array.isArray(policy.routinePaths) || !Array.isArray(policy.humanPaths) ||
      !Array.isArray(policy.reviewers) || !Array.isArray(policy.requiredChecks) ||
      policy.requiredChecks.length === 0) fail("Unsupported or incomplete trusted policy");
  for (const path of [...policy.routinePaths, ...policy.humanPaths]) {
    if (!canonicalPath(path.endsWith("/") ? path.slice(0, -1) : path)) fail("Invalid policy path");
  }
  for (const reviewer of policy.reviewers) {
    if (!integer(reviewer.userId) || !integer(reviewer.appId) || reviewer.appId === 15368 ||
        !reviewer.login?.endsWith("[bot]") || !reviewer.appSlug || !reviewer.checkName) {
      fail("Independent reviewer identity is incomplete");
    }
  }
  for (const check of policy.requiredChecks) {
    if (!integer(check.appId) || !check.name || !check.appSlug ||
        (check.appId === 15368 && !canonicalPath(check.workflow))) fail("Invalid check source");
  }
}

export const inventoryDigest = (files) => createHash("sha256").update(JSON.stringify(
  files.map(({ filename, status, sha }) => ({ filename, status, sha })).sort((a, b) => (a.filename < b.filename ? -1 : a.filename > b.filename ? 1 : 0)),
)).digest("hex");

const matches = (path, rule) => rule.endsWith("/") ? path.startsWith(rule) : path === rule;

export function classify(files, policy) {
  validatePolicy(policy);
  if (!Array.isArray(files) || files.length === 0) fail("No complete change inventory");
  const reasons = [];
  const seen = new Set();
  for (const file of files) {
    if (!canonicalPath(file.filename) || seen.has(file.filename) ||
        !["added", "modified", "removed", "renamed"].includes(file.status)) {
      fail("Invalid, duplicate, or unsupported changed path");
    }
    seen.add(file.filename);
    const paths = [file.filename];
    if (file.status === "renamed") {
      if (!canonicalPath(file.previous_filename)) fail("Rename lacks its original path");
      paths.push(file.previous_filename);
    }
    if (file.status === "removed" || file.status === "renamed") reasons.push("deletion-or-rename");
    for (const path of paths) {
      if (policy.humanPaths.some((rule) => matches(path, rule)) ||
          /(^|\/)(AGENTS\.md|CLAUDE\.md|CODEOWNERS)$/.test(path)) reasons.push("human-controlled-path");
      else if (!policy.routinePaths.some((rule) => matches(path, rule))) reasons.push("unclassified-path");
    }
  }
  return { risk: reasons.length ? "human-required" : "routine-candidate", reasons: [...new Set(reasons)].sort() };
}

function latestChecks(checks, spec, head) {
  const records = checks.filter((check) => check.name === spec.name && check.app?.id === spec.appId);
  if (!records.length) fail(`Missing check: ${spec.name}`);
  if (records.some((record) => !integer(record.id))) fail("Malformed check identity");
  // A newer queued/failed run invalidates an older success. Never select a convenient pass.
  const newest = records.sort((a, b) => b.id - a.id)[0];
  if (!integer(newest.id) || newest.app.slug !== spec.appSlug || newest.head_sha !== head ||
      newest.status !== "completed" || newest.conclusion !== "success") fail(`Unsuccessful check: ${spec.name}`);
  return newest;
}

export async function evaluate({ policy, policySha, prNumber, request }) {
  validatePolicy(policy);
  if (!SHA.test(policySha) || !integer(prNumber)) fail("Exact policy commit and PR number are required");
  const root = `/repos/${REPOSITORY}`;
  const get = (path) => request(`${root}/${path}`);
  const pages = async (path, key) => {
    const rows = [];
    for (let page = 1; page <= 10; page += 1) {
      const result = await get(`${path}${path.includes("?") ? "&" : "?"}per_page=100&page=${page}`);
      const batch = key ? result?.[key] : result;
      if (!Array.isArray(batch) || batch.some((row) => !row || typeof row !== "object")) fail("Incomplete API evidence");
      rows.push(...batch);
      if (batch.length < 100) {
        if (key && result.total_count !== rows.length) fail("Incomplete API evidence count");
        return rows;
      }
    }
    fail("Evidence exceeds bounded pagination; human review required");
  };
  const main = await get("git/ref/heads/main");
  if (main.object?.sha !== policySha) fail("Trusted policy is not current protected main");
  const pr = await get(`pulls/${prNumber}`);
  const head = pr.head?.sha;
  if (pr.number !== prNumber || pr.state !== "open" || pr.draft !== false || !SHA.test(head) ||
      pr.base?.ref !== "main" || pr.base.sha !== policySha ||
      pr.base.repo?.full_name !== REPOSITORY || pr.head.repo?.full_name !== REPOSITORY ||
      !integer(pr.base.repo?.id) || !integer(pr.head.repo?.id) ||
      !integer(pr.user?.id) || !integer(pr.changed_files) || !integer(pr.commits)) {
    fail("PR is not a complete, non-draft, same-repository main candidate");
  }
  const files = await pages(`pulls/${prNumber}/files`);
  if (files.length !== pr.changed_files) fail("Changed-file inventory is incomplete");
  const classification = classify(files, policy);
  const result = {
    schema: 1, mode: "shadow", repository: REPOSITORY, pullRequest: prNumber,
    policyCommit: policySha, headCommit: head, ...classification,
    source: "human-required", release: "human-required", deployment: "human-required",
    authorized: false,
  };
  if (classification.risk !== "routine-candidate") return result;

  const tree = await get(`git/trees/${head}?recursive=1`);
  if (tree.sha !== head && !SHA.test(tree.sha)) fail("Invalid source tree identity");
  if (tree.truncated !== false || !Array.isArray(tree.tree)) fail("Incomplete source tree");
  for (const file of files) {
    const entries = tree.tree.filter((entry) => entry.path === file.filename);
    if (entries.length !== 1 || entries[0].mode !== "100644" || entries[0].type !== "blob" ||
        !SHA.test(file.sha) || entries[0].sha !== file.sha) fail("Changed path is not an exact regular source blob");
  }
  const commits = await pages(`pulls/${prNumber}/commits`);
  if (commits.length !== pr.commits || !commits.some((commit) => commit.sha === head) ||
      new Set(commits.map((commit) => commit.sha)).size !== commits.length) fail("Incomplete commit inventory");
  const contributors = new Set([pr.user.id]);
  for (const commit of commits) {
    if (!SHA.test(commit.sha) || commit.commit?.verification?.verified !== true ||
        commit.commit.verification.reason !== "valid" || !integer(commit.author?.id) ||
        !integer(commit.committer?.id) || /(^|\n)Co-authored-by:/im.test(commit.commit?.message ?? "")) fail("Unsigned commit or unresolved contributor identity");
    contributors.add(commit.author.id);
    contributors.add(commit.committer.id);
  }
  const reviews = await pages(`pulls/${prNumber}/reviews`);
  const latest = new Map();
  for (const review of reviews) {
    if (!integer(review.id) || !integer(review.user?.id) ||
        !["APPROVED", "CHANGES_REQUESTED", "DISMISSED", "COMMENTED", "PENDING"].includes(review.state) ||
        (review.state !== "PENDING" && !Number.isFinite(Date.parse(review.submitted_at)))) {
      fail("Unresolved review identity, state, or submission time");
    }
  }
  for (const review of reviews.filter((r) => r.state !== "PENDING").sort((a, b) =>
    Date.parse(a.submitted_at) - Date.parse(b.submitted_at) || a.id - b.id)) {
    if (["APPROVED", "CHANGES_REQUESTED", "DISMISSED"].includes(review.state)) latest.set(review.user.id, review);
  }
  if ([...latest.values()].some((review) => review.state === "CHANGES_REQUESTED")) fail("Outstanding requested changes");
  const checks = await pages(`commits/${head}/check-runs?filter=all`, "check_runs");
  const suites = await pages(`commits/${head}/check-suites`, "check_suites");
  const requiredApps = new Set([...policy.reviewers, ...policy.requiredChecks].map((source) => source.appId));
  for (const suite of suites.filter((item) => requiredApps.has(item.app?.id))) {
    if (!integer(suite.id) || suite.head_sha !== head || suite.status !== "completed" ||
        suite.conclusion !== "success") fail("Relevant check suite is pending, failed, or unresolved");
  }
  const reviewer = policy.reviewers.find((identity) => {
    const review = latest.get(identity.userId);
    return review?.state === "APPROVED" && review.commit_id === head &&
      review.user.type === "Bot" && review.user.login === identity.login && !contributors.has(identity.userId);
  });
  if (!reviewer) fail("No independent exact-head reviewer approval");
  const approval = latest.get(reviewer.userId);
  if (approval.html_url !== `https://github.com/${REPOSITORY}/pull/${prNumber}#pullrequestreview-${approval.id}`) {
    fail("Review attestation has an unexpected source");
  }

  const verifiedSuites = new Map();
  const verifySuite = async (check) => {
    const id = check.check_suite?.id;
    if (!integer(id) || !suites.some((suite) => suite.id === id)) fail("Check has no inventoried source suite");
    if (!verifiedSuites.has(id)) verifiedSuites.set(id, await get(`check-suites/${id}`));
    const suite = verifiedSuites.get(id);
    if (suite.id !== id || suite.app?.id !== check.app.id || suite.app?.slug !== check.app.slug ||
        suite.head_sha !== head || suite.status !== "completed" || suite.conclusion !== "success") {
      fail("Check suite is stale, rerequested, failed, or has another source");
    }
  };
  const reviewCheck = latestChecks(checks, {
    name: reviewer.checkName, appId: reviewer.appId, appSlug: reviewer.appSlug,
  }, head);
  if (!reviewCheck.pull_requests?.some((item) => item.number === prNumber &&
      item.head?.sha === head && item.base?.sha === policySha &&
      item.base?.repo?.id === pr.base.repo.id)) fail("Review check is not bound to this PR");
  await verifySuite(reviewCheck);
  // A green bot status (including rate-limited/skipped review) or an APPROVED
  // override is insufficient. The independent app must attest completed coverage.
  const attestation = JSON.parse(reviewCheck.output?.summary ?? "null");
  const expected = {
    schema: "mtg.independent-review/v1", repository: REPOSITORY, pullRequest: prNumber,
    headCommit: head, policyCommit: policySha, reviewId: approval.id,
    inventorySha256: inventoryDigest(files), reviewed: "complete", outcome: "approved",
    override: false, risks: [],
  };
  if (!attestation || Object.keys(attestation).length !== Object.keys(expected).length ||
      Object.entries(expected).some(([key, value]) => JSON.stringify(attestation[key]) !== JSON.stringify(value))) {
    fail("Independent review attestation is missing, changed, incomplete, overridden, or risk-bearing");
  }
  if (reviewCheck.external_id !== "sha256:" + createHash("sha256").update(JSON.stringify(expected)).digest("hex")) {
    fail("Independent review attestation digest differs");
  }
  const verifiedChecks = [];
  for (const spec of policy.requiredChecks) {
    const check = latestChecks(checks, spec, head);
    await verifySuite(check);
    if (spec.appId === 15368) {
      const escaped = REPOSITORY.replaceAll("/", "\\/");
      const match = check.details_url?.match(new RegExp(`^https://github\\.com/${escaped}/actions/runs/([0-9]+)/job/([0-9]+)$`));
      if (!match || Number(match[2]) !== check.id) fail(`No workflow provenance: ${spec.name}`);
      const run = await get(`actions/runs/${match[1]}`);
      if (!integer(run.workflow_id)) fail("Workflow run has no trusted workflow identity");
      const workflow = await get(`actions/workflows/${run.workflow_id}`);
      if (workflow.id !== run.workflow_id || workflow.path !== spec.workflow ||
          run.check_suite_id !== check.check_suite.id ||
          run.id !== Number(match[1]) || run.head_sha !== head ||
          run.repository?.full_name !== REPOSITORY || run.head_repository?.full_name !== REPOSITORY ||
          run.event !== "pull_request" || run.status !== "completed" || run.conclusion !== "success" ||
          !run.pull_requests?.some((item) => item.number === prNumber && item.head?.sha === head &&
            item.base?.sha === policySha)) fail(`Untrusted workflow evidence: ${spec.name}`);
    }
    verifiedChecks.push({ name: spec.name, id: check.id, appId: spec.appId });
  }
  // Reviews, check runs and suites are mutable even when the head stays fixed.
  const refreshedReviews = await pages(`pulls/${prNumber}/reviews`);
  const refreshedChecks = await pages(`commits/${head}/check-runs?filter=all`, "check_runs");
  const refreshedSuites = await pages(`commits/${head}/check-suites`, "check_suites");
  if (JSON.stringify(refreshedSuites) !== JSON.stringify(suites) ||
      JSON.stringify(refreshedReviews) !== JSON.stringify(reviews) ||
      JSON.stringify(refreshedChecks) !== JSON.stringify(checks)) fail("Review or check evidence changed during evaluation");
  for (const [id, suite] of verifiedSuites) {
    if (JSON.stringify(await get(`check-suites/${id}`)) !== JSON.stringify(suite)) {
      fail("Check suite changed during evaluation");
    }
  }
  // Read back after collecting. Reports are head/base-bound observations, never reusable grants.
  const current = await get(`pulls/${prNumber}`);
  const currentMain = await get("git/ref/heads/main");
  if (current.head?.sha !== head || current.base?.sha !== policySha || current.state !== "open" ||
      current.draft !== false || currentMain.object?.sha !== policySha) fail("Candidate or policy changed during evaluation");
  return {
    ...result, source: "eligible-after-reviewed-activation", reasons: ["shadow-mode-no-authority"],
    review: { id: latest.get(reviewer.userId).id, userId: reviewer.userId, appId: reviewer.appId, checkId: reviewCheck.id, url: approval.html_url },
    checks: verifiedChecks,
  };
}

async function main() {
  // Inputs are event/runtime identities, not a caller-supplied attestation file.
  const event = JSON.parse(await readFile(process.env.GITHUB_EVENT_PATH, "utf8"));
  if (!["pull_request_target", "workflow_run", "workflow_dispatch"].includes(process.env.GITHUB_EVENT_NAME) ||
      process.env.GITHUB_REPOSITORY !== REPOSITORY || process.env.GITHUB_REF !== "refs/heads/main") {
    fail("Shadow evaluation requires a protected-main event context");
  }
  let prNumber = event.pull_request?.number;
  if (process.env.GITHUB_EVENT_NAME === "workflow_dispatch") prNumber = Number(event.inputs?.pull_request);
  if (process.env.GITHUB_EVENT_NAME === "workflow_run") {
    const run = event.workflow_run;
    if (run?.event !== "pull_request" || run.head_repository?.full_name !== REPOSITORY ||
        run.pull_requests?.length !== 1) fail("Workflow event is not one same-repository PR");
    prNumber = run.pull_requests[0].number;
  }
  const policy = JSON.parse(await readFile(new URL("../bounded-delivery-policy.json", import.meta.url), "utf8"));
  const request = async (path) => {
    const response = await fetch(`https://api.github.com${path}`, {
      headers: { Authorization: `Bearer ${process.env.GH_TOKEN}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" },
      signal: AbortSignal.timeout(30_000),
    });
    if (!response.ok) fail(`Evidence unavailable: HTTP ${response.status}`);
    return response.json();
  };
  console.log(JSON.stringify(await evaluate({ policy, policySha: process.env.GITHUB_SHA,
    prNumber, request }), null, 2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(JSON.stringify({ mode: "shadow", authorized: false, source: "human-required",
      release: "human-required", deployment: "human-required", reason: error.message }));
    process.exitCode = 1;
  });
}
