import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const repository = "Midtown-Technology-Group/bifrost";

export async function authorizeMergeQueue({ eventName, event, sha, repo, request }) {
  if (repo !== repository) throw new Error("Unexpected repository");
  // GitHub requires the same check before admission and on the merge group.
  // Authorization happens on the live merge group, where its enqueuer exists.
  if (eventName === "pull_request" && event.pull_request?.base?.ref === "main") {
    return "Queue admission prerequisite passed; authorization is checked on the merge group.";
  }
  if (eventName !== "merge_group" || event.action !== "checks_requested") {
    throw new Error("Unexpected event");
  }
  const group = event.merge_group;
  if (group?.base_ref !== "refs/heads/main" || group.head_sha !== sha) {
    throw new Error("Merge group does not match this main candidate");
  }
  const response = await request("/graphql", {
    query: `query {
      repository(owner: "Midtown-Technology-Group", name: "bifrost") {
        mergeQueue(branch: "main") {
          entries(first: 100) {
            pageInfo { hasNextPage }
            nodes {
              enqueuer { __typename login ... on User { databaseId } }
              headCommit { oid }
              pullRequest { number baseRefName }
            }
          }
        }
      }
    }`,
  });
  if (response.errors) throw new Error("GitHub could not read the merge queue");
  const entries = response.data?.repository?.mergeQueue?.entries;
  if (!entries || entries.pageInfo.hasNextPage) throw new Error("Incomplete merge queue response");
  // Only the first entry may merge. Later PRs may already be waiting in the queue.
  if (entries.nodes[0]?.headCommit?.oid !== sha) {
    throw new Error("Candidate is not the first live merge queue entry");
  }
  const entry = entries.nodes[0];
  if (entry.pullRequest.baseRefName !== "main" || entry.enqueuer?.__typename !== "User") {
    throw new Error("An MTG administrator must enqueue this main PR");
  }
  const permission = await request(
    `/repos/${repository}/collaborators/${encodeURIComponent(entry.enqueuer.login)}/permission`,
  );
  if (permission.permission !== "admin" || permission.user?.id !== entry.enqueuer.databaseId) {
    throw new Error("The enqueuer does not have repository administrator permission");
  }
  return `${entry.enqueuer.login} authorized PR #${entry.pullRequest.number} at ${sha}.`;
}

async function main() {
  const event = JSON.parse(await readFile(process.env.GITHUB_EVENT_PATH, "utf8"));
  const request = async (path, body) => {
    const response = await fetch(`https://api.github.com${path}`, {
      method: body ? "POST" : "GET",
      headers: {
        Authorization: `Bearer ${process.env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(30_000),
    });
    if (!response.ok) throw new Error(`GitHub API returned HTTP ${response.status}`);
    return response.json();
  };
  console.log(await authorizeMergeQueue({
    eventName: process.env.GITHUB_EVENT_NAME,
    event,
    sha: process.env.GITHUB_SHA,
    repo: process.env.GITHUB_REPOSITORY,
    request,
  }));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
