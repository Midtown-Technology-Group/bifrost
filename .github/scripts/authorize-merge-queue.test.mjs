import assert from "node:assert/strict";
import test from "node:test";
import { authorizeMergeQueue } from "./authorize-merge-queue.mjs";

function fixture() {
  const entry = {
    enqueuer: { __typename: "User", login: "MTG-Thomas", databaseId: 87775189 },
    headCommit: { oid: "candidate" },
    pullRequest: { number: 714, baseRefName: "main" },
  };
  const queue = { data: { repository: { mergeQueue: { entries: {
    pageInfo: { hasNextPage: false }, nodes: [entry],
  } } } } };
  const permission = { permission: "admin", user: { id: 87775189 } };
  const calls = [];
  const input = {
    repo: "Midtown-Technology-Group/bifrost", sha: "candidate", eventName: "merge_group",
    event: { action: "checks_requested", merge_group: { base_ref: "refs/heads/main", head_sha: "candidate" } },
    request: async (path) => {
      calls.push(path);
      return path === "/graphql" ? queue : permission;
    },
  };
  return { input, entry, queue, permission, calls };
}

test("authorizes the administrator who queued the exact live candidate", async () => {
  const { input, calls } = fixture();
  assert.match(await authorizeMergeQueue(input), /MTG-Thomas authorized PR #714 at candidate/);
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

test("later waiting PRs do not block the authorized first candidate", async () => {
  const { input, queue } = fixture();
  queue.data.repository.mergeQueue.entries.nodes.push({ headCommit: null });
  assert.match(await authorizeMergeQueue(input), /authorized PR #714/);
});
