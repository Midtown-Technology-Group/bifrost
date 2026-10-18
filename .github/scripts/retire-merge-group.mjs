import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";

const REPOSITORY = "Midtown-Technology-Group/bifrost";
const ACTIVE = new Set(["queued", "in_progress", "waiting", "requested", "pending"]);
const WORKFLOWS = new Set([".github/workflows/ci.yml", ".github/workflows/codeql.yml"]);
const SHA = /^[a-f0-9]{40}$/;

export function candidate(run) {
  const match = /^gh-readonly-queue\/main\/pr-([1-9][0-9]*)-([a-f0-9]{40})$/.exec(run.head_branch ?? "");
  if (run.repository?.full_name !== REPOSITORY || run.event !== "merge_group"
      || !WORKFLOWS.has(run.path) || !SHA.test(run.head_sha ?? "") || !match
      || !Number.isSafeInteger(run.id) || run.id <= 0
      || !Number.isSafeInteger(run.run_attempt) || run.run_attempt <= 0) {
    throw new Error("Unsupported repository, workflow or merge-group identity");
  }
  const number = Number(match[1]);
  if (!Number.isSafeInteger(number)) throw new Error("Invalid pull request identity");
  return number;
}

export function authorizeRetirement(run, queue, history) {
  const number = candidate(run);
  if (!ACTIVE.has(run.status)) throw new Error("Run is no longer active");
  if (!queue || queue.pageInfo?.hasNextPage !== false || !Array.isArray(queue.nodes)
      || queue.nodes.some(entry => !Number.isSafeInteger(entry.pullRequest?.number)
        || entry.pullRequest.number <= 0
        || (!SHA.test(entry.headCommit?.oid ?? "")
          && !(entry.headCommit === null && entry.state === "QUEUED")))) {
    throw new Error("Incomplete live queue evidence");
  }
  if (queue.nodes.some(entry => entry.headCommit?.oid === run.head_sha || entry.pullRequest.number === number)) {
    throw new Error("Candidate is still in the live queue");
  }
  if (history?.number !== number || history.timelineItems?.pageInfo?.hasPreviousPage !== false
      || !Array.isArray(history.timelineItems.nodes)) throw new Error("Incomplete removal evidence");
  const events = history.timelineItems.nodes.filter(event => event.beforeCommit?.oid === run.head_sha);
  const removal = events.at(-1);
  if (!removal || removal.reason !== "checks_timed_out"
      || !Number.isFinite(Date.parse(run.created_at)) || !Number.isFinite(Date.parse(removal.createdAt))
      || Date.parse(removal.createdAt) < Date.parse(run.created_at)) {
    throw new Error("Exact candidate has no authoritative timeout removal");
  }
  return { repository: REPOSITORY, run_id: run.id, attempt: run.run_attempt,
    candidate_sha: run.head_sha, pr: number, removed_at: removal.createdAt, reason: removal.reason };
}

function request(method, path, body) {
  const args = ["api", "--method", method, path];
  if (body) args.push("--input", "-");
  return execFileSync("gh", args, { input: body ? JSON.stringify(body) : undefined,
    maxBuffer: 100 * 1024 * 1024, stdio: ["pipe", "pipe", "pipe"] });
}

export async function retireRun({ runId, apply = false, save, api }) {
  if (!Number.isSafeInteger(runId) || runId <= 0) throw new Error("Invalid run ID");
  const path = `repos/${REPOSITORY}/actions/runs/${runId}`;
  const run = await api("GET", path);
  save("run-before.json", run);
  const number = candidate(run);
  let proofNumber = 0;
  const query = `query { repository(owner:"Midtown-Technology-Group",name:"bifrost") {
    mergeQueue(branch:"main") { entries(first:100) { pageInfo {hasNextPage}
      nodes {state headCommit {oid} pullRequest {number}} } }
    pullRequest(number:${number}) { number timelineItems(last:100,itemTypes:[REMOVED_FROM_MERGE_QUEUE_EVENT]) {
      pageInfo {hasPreviousPage} nodes {... on RemovedFromMergeQueueEvent {createdAt reason beforeCommit {oid}}}
    } }
  } }`;
  async function liveProof(current) {
    const response = await api("POST", "graphql", { query });
    save(`live-proof-${++proofNumber}.json`, response);
    if (response.errors || !response.data?.repository) throw new Error("GitHub queue read failed");
    const repo = response.data.repository;
    return authorizeRetirement(current, repo.mergeQueue?.entries, repo.pullRequest);
  }
  const proof = await liveProof(run);
  save("plan.json", proof);
  if (!apply) return { ...proof, outcome: "planned", cancelled: false };

  // Capture completed-job logs and every job's original status before mutation.
  // Unfinished job logs remain on GitHub and are collected after terminal readback.
  const jobs = [];
  for (let page = 1; page <= 10; page++) {
    const batch = await api("GET", `${path}/attempts/${run.run_attempt}/jobs?per_page=100&page=${page}`);
    if (!Array.isArray(batch.jobs) || !Number.isSafeInteger(batch.total_count)) throw new Error("Invalid job inventory");
    jobs.push(...batch.jobs);
    if (jobs.length === batch.total_count) break;
    if (batch.jobs.length < 100 || page === 10) throw new Error("Incomplete job inventory");
  }
  save("jobs-before.json", jobs);
  for (const job of jobs) {
    if (job.status !== "completed" || job.conclusion === "skipped") continue;
    if (!Number.isSafeInteger(job.id) || job.id <= 0) throw new Error("Invalid job identity");
    save(`job-${job.id}.log`, await api("GET", `repos/${REPOSITORY}/actions/jobs/${job.id}/logs`, undefined, true));
  }
  const fresh = await api("GET", path);
  save("run-rechecked.json", fresh);
  if (fresh.id !== run.id || fresh.head_sha !== run.head_sha || fresh.run_attempt !== run.run_attempt
      || fresh.head_branch !== run.head_branch || fresh.path !== run.path) throw new Error("Run identity changed");
  await liveProof(fresh);
  save("cancel-intent.json", { ...proof, recorded_at: new Date().toISOString() });
  // Never retry this write: a transport failure may hide an accepted cancellation.
  try {
    await api("POST", `${path}/cancel`);
  } catch {
    save("cancel-result.json", { outcome: "unknown", next: "Read original run; do not resend cancellation" });
    throw new Error("Cancellation outcome unknown; inspect retained receipt before any action");
  }
  save("cancel-result.json", { ...proof, outcome: "cancellation_requested", cancelled: false });
  const after = await api("GET", path);
  save("run-after.json", after);
  if (after.id !== run.id || after.head_sha !== run.head_sha || after.run_attempt !== run.run_attempt) {
    throw new Error("Cancellation submitted but readback identity changed; reconcile original attempt");
  }
  const result = { ...proof, outcome: after.status === "completed" ? after.conclusion : "cancellation_requested",
    cancelled: after.status === "completed" && after.conclusion === "cancelled" };
  save("cancel-result.json", result);
  return result;
}

async function main() {
  const args = process.argv.slice(2);
  const apply = args.includes("--apply");
  const remaining = args.filter(arg => arg !== "--apply");
  if (remaining.length !== 2 || !/^[1-9][0-9]*$/.test(remaining[0])
      || args.filter(arg => arg === "--apply").length > 1) {
    throw new Error("Usage: node .github/scripts/retire-merge-group.mjs RUN_ID NEW_PRIVATE_EVIDENCE_DIR [--apply]");
  }
  const directory = resolve(remaining[1]);
  mkdirSync(directory, { mode: 0o700 }); // Refuse reuse: do not overwrite an uncertain mutation receipt.
  const save = (name, data) => writeFileSync(join(directory, name),
    Buffer.isBuffer(data) ? data : `${JSON.stringify(data, null, 2)}\n`, { mode: 0o600 });
  try {
    const result = await retireRun({ runId: Number(remaining[0]), apply, save,
      api: async (method, path, body, binary = false) => {
        const raw = request(method, path, body);
        return binary ? raw : raw.length ? JSON.parse(raw) : null;
      } });
    console.log(JSON.stringify(result));
  } catch (error) {
    save("error.json", { outcome: "stopped", message: error.message });
    throw error;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(() => {
    console.error("Retirement stopped; inspect the private evidence directory. No automatic retry.");
    process.exitCode = 1;
  });
}
