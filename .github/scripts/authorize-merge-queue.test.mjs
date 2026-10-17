import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { authorizeMergeQueue, githubRequestUrl } from "./authorize-merge-queue.mjs";

test("candidate-controlled queue gate files require trusted-owner review", () => {
  const owners = readFileSync(new URL("../CODEOWNERS", import.meta.url), "utf8");
  const trustedTeam = "@Midtown-Technology-Group/bifrost-main-maintainers";
  const protectedPaths = [
    "/.github/CODEOWNERS",
    "/.github/workflows/mtg-queue-authorization.yml",
    "/.github/scripts/authorize-merge-queue.mjs",
    "/.github/scripts/authorize-merge-queue.test.mjs",
    "/docs/plans/2026-09-15-mtg-queue-authorization.md",
  ];
  const rules = owners
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"));
  assert.deepEqual(
    rules.slice(-protectedPaths.length),
    protectedPaths.map((path) => `${path} ${trustedTeam}`),
    "the protected exact-match rules must remain last so no later rule can override them",
  );
});

test("permission lookups cannot escape the fixed GitHub API route", () => {
  assert.equal(githubRequestUrl("/graphql").href, "https://api.github.com/graphql");
  assert.equal(githubRequestUrl("/repos/Midtown-Technology-Group/bifrost/collaborators/MTG-Thomas/permission").hostname, "api.github.com");
  for (const path of ["//other.example", "https://other.example", "/graphql?other=1", "/graphql\n", "/repos/Midtown-Technology-Group/bifrost/collaborators/../permission"]) {
    assert.throws(() => githubRequestUrl(path), /Unexpected/);
  }
});

function fixture() {
  const entry = {
    enqueuer: { __typename: "User", login: "MTG-Thomas", databaseId: 87775189 },
    headCommit: { oid: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" },
    pullRequest: { number: 714, baseRefName: "main" },
  };
  const queue = { data: { repository: { mergeQueue: { entries: {
    pageInfo: { hasNextPage: false }, nodes: [entry],
  } } } } };
  const permission = { permission: "admin", user: { id: 87775189 } };
  const calls = [];
  const input = {
    repo: "Midtown-Technology-Group/bifrost", sha: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", eventName: "merge_group",
    event: { action: "checks_requested", merge_group: { base_ref: "refs/heads/main", head_sha: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" } },
    request: async (path) => {
      calls.push(path);
      return path === "/graphql" ? queue : permission;
    },
  };
  return { input, entry, queue, permission, calls };
}

test("authorizes the administrator who queued the exact live candidate", async () => {
  const { input, calls } = fixture();
  assert.match(await authorizeMergeQueue(input), /MTG-Thomas authorized PR #714 at a{40}/);
  assert.deepEqual(calls, ["/graphql", "/repos/Midtown-Technology-Group/bifrost/collaborators/MTG-Thomas/permission"]);
});

test("PR prerequisite does not require an entry before the PR can be queued", async () => {
  const { input, calls } = fixture();
  input.eventName = "pull_request";
  input.event = { pull_request: { base: { ref: "main" } } };
  assert.match(await authorizeMergeQueue(input), /authorization is checked on the merge group/);
  assert.deepEqual(calls, []);
});

test("write access does not authorize a merge", async () => {
  const { input, permission } = fixture();
  permission.permission = "write";
  await assert.rejects(authorizeMergeQueue(input), /administrator permission/);
});

test("does not accept a permission response for a different identity", async () => {
  const { input, permission } = fixture();
  permission.user.id = 1;
  await assert.rejects(authorizeMergeQueue(input), /administrator permission/);
});

test("does not authorize bots", async () => {
  const { input, entry } = fixture();
  entry.enqueuer.__typename = "Bot";
  await assert.rejects(authorizeMergeQueue(input), /MTG administrator/);
});

for (const login of ["../other", "MTG-Thomas\nforged approval", "MTG-Thomas\n", "MTG-Thomas\r", "bad/user", "-option", ""] ) {
  test(`rejects malformed actor ${JSON.stringify(login)} before permission lookup`, async () => {
    const { input, entry, calls } = fixture();
    entry.enqueuer.login = login;
    await assert.rejects(authorizeMergeQueue(input), /Invalid merge queue/);
    assert.deepEqual(calls, ["/graphql"]);
  });
}

for (const [name, alter] of [
  ["stale queue candidate", ({ entry }) => { entry.headCommit.oid = "old"; }],
  ["removed queue entry", ({ queue }) => { queue.data.repository.mergeQueue.entries.nodes = []; }],
  ["candidate behind another PR", ({ queue }) => { queue.data.repository.mergeQueue.entries.nodes.unshift({ headCommit: { oid: "earlier" } }); }],
  ["incomplete pagination", ({ queue }) => { queue.data.repository.mergeQueue.entries.pageInfo.hasNextPage = true; }],
  ["GraphQL failure", ({ queue }) => { queue.errors = [{ message: "denied" }]; }],
  ["wrong base branch", ({ input }) => { input.event.merge_group.base_ref = "refs/heads/other"; }],
  ["mismatched event SHA", ({ input }) => { input.event.merge_group.head_sha = "old"; }],
  ["wrong repository", ({ input }) => { input.repo = "gobifrost/bifrost"; }],
  ["unsupported trigger", ({ input }) => { input.eventName = "workflow_dispatch"; }],
]) {
  test(`rejects ${name}`, async () => {
    const state = fixture();
    alter(state);
    await assert.rejects(authorizeMergeQueue(state.input));
  });
}

test("API failure fails the gate", async () => {
  const { input } = fixture();
  input.request = async () => { throw new Error("HTTP 403"); };
  await assert.rejects(authorizeMergeQueue(input), /HTTP 403/);
});

test("CLI fails closed without publishing malicious API errors or credentials", () => {
  const directory = mkdtempSync(join(tmpdir(), "merge-queue-log-"));
  try {
    const { input } = fixture();
    const eventPath = join(directory, "event.json");
    writeFileSync(eventPath, JSON.stringify(input.event));
    const bootstrap = `
      import { pathToFileURL } from "node:url";
      globalThis.fetch = async () => { throw new Error("forged approval\\ncredential: test-only"); };
      await import(pathToFileURL(process.argv[1]).href);
    `;
    const result = spawnSync(process.execPath, ["--input-type=module", "-e", bootstrap,
      fileURLToPath(new URL("./authorize-merge-queue.mjs", import.meta.url))], {
      encoding: "utf8",
      env: { GITHUB_EVENT_PATH: eventPath, GITHUB_EVENT_NAME: input.eventName,
        GITHUB_SHA: input.sha, GITHUB_REPOSITORY: input.repo, GH_TOKEN: "test-only" },
    });
    assert.equal(result.status, 1);
    assert.equal(result.stdout, "");
    assert.doesNotMatch(result.stderr, /forged approval|test-only/);
    assert.equal(result.stderr.trim().split("\n").length, 1);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("later waiting PRs do not block the authorized first candidate", async () => {
  const { input, queue } = fixture();
  queue.data.repository.mergeQueue.entries.nodes.push({ headCommit: null });
  assert.match(await authorizeMergeQueue(input), /authorized PR #714/);
});
