import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, statSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { authorizeRetirement, candidate, retireRun } from "./retire-merge-group.mjs";

function fixture() {
  const run = { id: 42, run_attempt: 1, event: "merge_group", status: "in_progress",
    repository: { full_name: "Midtown-Technology-Group/bifrost" }, path: ".github/workflows/ci.yml",
    head_sha: "a".repeat(40), head_branch: `gh-readonly-queue/main/pr-123-${"b".repeat(40)}`,
    created_at: "2026-10-18T20:00:00Z" };
  const queue = { pageInfo: { hasNextPage: false }, nodes: [] };
  const history = { number: 123, timelineItems: { pageInfo: { hasPreviousPage: false }, nodes: [
    { createdAt: "2026-10-18T21:00:00Z", reason: "checks_timed_out", beforeCommit: { oid: run.head_sha } },
  ] } };
  return { run, queue, history };
}

test("authorizes only an exact retired candidate, independently of its later green checks", () => {
  const { run, queue, history } = fixture();
  assert.equal(candidate(run), 123);
  assert.equal(authorizeRetirement(run, queue, history).candidate_sha, run.head_sha);
});

for (const [name, change] of [
  ["other repository", x => { x.run.repository.full_name = "gobifrost/bifrost"; }],
  ["PR run", x => { x.run.event = "pull_request"; }],
  ["other workflow", x => { x.run.path = ".github/workflows/release.yml"; }],
  ["other base", x => { x.run.head_branch = x.run.head_branch.replace("main", "other"); }],
  ["malformed SHA", x => { x.run.head_sha += "\n"; }],
  ["terminal run", x => { x.run.status = "completed"; }],
  ["live candidate", x => { x.queue.nodes.push({ headCommit: { oid: x.run.head_sha }, pullRequest: { number: 456 } }); }],
  ["truncated queue", x => { x.queue.pageInfo.hasNextPage = true; }],
  ["unknown queue head", x => { x.queue.nodes.push({ headCommit: null }); }],
  ["missing queue", x => { x.queue = null; }],
  ["truncated history", x => { x.history.timelineItems.pageInfo.hasPreviousPage = true; }],
  ["other PR", x => { x.history.number++; }],
  ["other retired SHA", x => { x.history.timelineItems.nodes[0].beforeCommit.oid = "c".repeat(40); }],
  ["failed checks", x => { x.history.timelineItems.nodes[0].reason = "failed_checks"; }],
  ["manual removal", x => { x.history.timelineItems.nodes[0].reason = "manual"; }],
  ["old removal", x => { x.history.timelineItems.nodes[0].createdAt = "2026-10-17T21:00:00Z"; }],
  ["unknown removal time", x => { x.history.timelineItems.nodes[0].createdAt = "invalid"; }],
]) {
  test(`refuses ${name}`, () => {
    const x = fixture(); change(x);
    assert.throws(() => authorizeRetirement(x.run, x.queue, x.history));
  });
}

function transport() {
  const state = fixture();
  const saved = new Map(); const calls = [];
  let reads = 0; let proofs = 0;
  const api = async (method, path) => {
    calls.push([method, path]);
    if (path === "graphql") {
      proofs++;
      return { data: { repository: { mergeQueue: { entries: state.queue }, pullRequest: state.history } } };
    }
    if (path.includes("/jobs?")) return { total_count: 1, jobs: [{ id: 7, status: "completed", conclusion: "success" }] };
    if (path.endsWith("/logs")) return Buffer.from("original job log");
    if (path.endsWith("/cancel")) return null;
    reads++;
    return { ...state.run, status: reads >= 3 ? "completed" : state.run.status,
      conclusion: reads >= 3 ? "cancelled" : null };
  };
  return { state, saved, calls, api, save: (name, data) => saved.set(name, structuredClone(data)),
    get reads() { return reads; }, get proofs() { return proofs; } };
}

test("default planning performs no cancellation or log download", async () => {
  const x = transport();
  const result = await retireRun({ runId: 42, ...x });
  assert.equal(result.outcome, "planned");
  assert.deepEqual(x.calls.map(c => c[0]), ["GET", "POST"]); // POST is GraphQL read only.
  assert.equal(x.calls[1][1], "graphql");
});

test("unbuilt QUEUED entries have no candidate SHA and do not consume a retired other PR", () => {
  const { run, queue, history } = fixture();
  queue.nodes.push({ state: "QUEUED", headCommit: null, pullRequest: { number: 456 } });
  assert.equal(authorizeRetirement(run, queue, history).pr, 123);
  queue.nodes[0].pullRequest.number = 123;
  assert.throws(() => authorizeRetirement(run, queue, history), /live queue/);
  queue.nodes[0].pullRequest.number = 456;
  queue.nodes[0].state = "AWAITING_CHECKS";
  assert.throws(() => authorizeRetirement(run, queue, history), /Incomplete/);
});

test("apply preserves completed logs and rechecks run and live queue before cancelling once", async () => {
  const x = transport();
  const result = await retireRun({ runId: 42, apply: true, ...x });
  assert.equal(result.cancelled, true);
  assert.equal(x.reads, 3);
  assert.equal(x.proofs, 2);
  assert.ok(x.saved.has("job-7.log"));
  assert.ok(x.saved.has("cancel-intent.json"));
  assert.equal(x.calls.filter(c => c[1].endsWith("/cancel")).length, 1);
  assert.ok(x.calls.findIndex(c => c[1].endsWith("/logs")) < x.calls.findIndex(c => c[1].endsWith("/cancel")));
});

for (const mode of ["logs unavailable", "new attempt", "candidate requeued", "incomplete jobs", "GraphQL denied"]) {
  test(`no cancellation when ${mode}`, async () => {
    const x = transport(); const original = x.api;
    x.api = async (method, path) => {
      const result = await original(method, path);
      if (mode === "logs unavailable" && path.endsWith("/logs")) throw new Error("not retained");
      if (mode === "new attempt" && x.reads === 2 && path.endsWith("/42")) result.run_attempt++;
      if (mode === "candidate requeued" && path === "graphql" && x.proofs === 2) {
        result.data.repository.mergeQueue.entries.nodes.push({ headCommit: { oid: x.state.run.head_sha }, pullRequest: { number: 123 } });
      }
      if (mode === "incomplete jobs" && path.includes("/jobs?")) result.total_count = 2;
      if (mode === "GraphQL denied" && path === "graphql") result.errors = [{ message: "denied" }];
      return result;
    };
    await assert.rejects(retireRun({ runId: 42, apply: true, ...x }));
    assert.equal(x.calls.filter(c => c[1].endsWith("/cancel")).length, 0);
  });
}

test("uncertain cancellation is recorded and never resent", async () => {
  const x = transport(); const original = x.api;
  x.api = async (method, path) => {
    const result = await original(method, path);
    if (path.endsWith("/cancel")) throw new Error("lost response");
    return result;
  };
  await assert.rejects(retireRun({ runId: 42, apply: true, ...x }), /outcome unknown/);
  assert.equal(x.saved.get("cancel-result.json").outcome, "unknown");
  assert.equal(x.calls.filter(c => c[1].endsWith("/cancel")).length, 1);
});

test("accepted cancellation stays recorded when terminal readback is unavailable", async () => {
  const x = transport(); const original = x.api;
  x.api = async (method, path) => {
    const result = await original(method, path);
    if (x.reads === 3) throw new Error("readback unavailable");
    return result;
  };
  await assert.rejects(retireRun({ runId: 42, apply: true, ...x }));
  assert.equal(x.saved.get("cancel-result.json").outcome, "cancellation_requested");
  assert.equal(x.calls.filter(c => c[1].endsWith("/cancel")).length, 1);
});

test("CLI uses fixed system gh despite hostile PATH, private receipts and a non-overwriting dry run", () => {
  const directory = mkdtempSync(join(tmpdir(), "retire-queue-cli-"));
  try {
    const { run, queue, history } = fixture();
    const bin = join(directory, "bin"); mkdirSync(bin);
    const gh = join(directory, "trusted-gh.mjs");
    writeFileSync(gh, `#!${process.execPath}\n
      import fs from "node:fs";
      const args = process.argv.slice(2);
      fs.appendFileSync(${JSON.stringify(join(directory, "calls"))}, JSON.stringify(args) + "\\n");
      if (args.at(-1) === "-") {
        const body = JSON.parse(fs.readFileSync(0, "utf8"));
        if (!body.query.startsWith("query ")) process.exit(2);
        console.log(${JSON.stringify(JSON.stringify({ data: { repository: { mergeQueue: { entries: queue }, pullRequest: history } } }))});
      } else console.log(${JSON.stringify(JSON.stringify(run))});
    `, { mode: 0o700 });
    // Intercept only the fixed system executable in the child process. Production
    // exposes no executable override; PATH contains a trap that must never run.
    writeFileSync(join(bin, "gh"), "#!/bin/sh\nexit 99\n", { mode: 0o700 });
    const preload = join(directory, "transport.mjs");
    writeFileSync(preload, `
      import childProcess from "node:child_process";
      import { syncBuiltinESMExports } from "node:module";
      const execute = childProcess.execFileSync;
      childProcess.execFileSync = (file, args, options) => {
        if (file !== "/usr/bin/gh") throw new Error("Unexpected executable: " + file);
        return execute(${JSON.stringify(gh)}, args, options);
      };
      syncBuiltinESMExports();
    `);
    const script = fileURLToPath(new URL("./retire-merge-group.mjs", import.meta.url));
    const evidence = join(directory, "evidence");
    const options = { encoding: "utf8", env: { ...process.env, PATH: `${bin}:${process.env.PATH}` } };
    const first = spawnSync(process.execPath, ["--import", preload, script, "42", evidence], options);
    assert.equal(first.status, 0, first.stderr);
    assert.equal(JSON.parse(first.stdout).outcome, "planned");
    assert.equal(statSync(evidence).mode & 0o777, 0o700);
    for (const name of readdirSync(evidence)) assert.equal(statSync(join(evidence, name)).mode & 0o777, 0o600);
    const calls = readFileSync(join(directory, "calls"), "utf8").trim().split("\n").map(JSON.parse);
    assert.deepEqual(calls.map(args => args[2]), ["GET", "POST"]);
    assert.equal(calls[1][3], "graphql");
    const originalPlan = readFileSync(join(evidence, "plan.json"), "utf8");
    const repeated = spawnSync(process.execPath, ["--import", preload, script, "42", evidence], options);
    assert.equal(repeated.status, 1);
    assert.equal(readFileSync(join(evidence, "plan.json"), "utf8"), originalPlan);
    const invalid = spawnSync(process.execPath, ["--import", preload, script, "42", join(directory, "invalid"), "--unexpected"], options);
    assert.equal(invalid.status, 1);
    assert.equal(invalid.stdout, "");
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
