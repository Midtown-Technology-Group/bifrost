import { readFile } from 'node:fs/promises';

export const repository = 'Midtown-Technology-Group/bifrost';
export const buildPath = '.github/workflows/dependabot-candidates.yml';
const sha = /^[a-f0-9]{40}$/;
const components = ['api', 'worker', 'client'];

// Only trusted default-branch code calls this. PR artifacts are data, never code.
export async function qualify({ github, runId, expectedAttempt, trustedWorkflow }) {
  const scope = { owner: 'Midtown-Technology-Group', repo: 'bifrost' };
  const { data: run } = await github.rest.actions.getWorkflowRun({ ...scope, run_id: runId });
  if (run.id !== runId || run.run_attempt !== expectedAttempt ||
      run.repository.full_name !== repository || run.event !== 'pull_request' ||
      run.path !== buildPath || run.actor.login !== 'dependabot[bot]' ||
      run.status !== 'completed' || run.conclusion !== 'success' || !sha.test(run.head_sha) ||
      run.pull_requests.length !== 1) throw new Error('Unqualified candidate producer');
  const { data: pr } = await github.rest.pulls.get({ ...scope, pull_number: run.pull_requests[0].number });
  if (pr.state !== 'open' || pr.user.login !== 'dependabot[bot]' ||
      pr.head.repo.full_name !== repository || pr.head.sha !== run.head_sha ||
      pr.base.ref !== 'main' || !sha.test(pr.base.sha) ||
      pr.base.sha !== run.pull_requests[0].base.sha) throw new Error('Stale or foreign candidate source');
  if (!sha.test(pr.merge_commit_sha)) throw new Error('Missing candidate merge commit');
  const { data: merge } = await github.rest.repos.getCommit({ ...scope, ref: pr.merge_commit_sha });
  if (merge.parents.length !== 2 || merge.parents[0].sha !== pr.base.sha || merge.parents[1].sha !== pr.head.sha) {
    throw new Error('Candidate workflow merge does not match source and base');
  }
  const { data: workflow } = await github.rest.repos.getContent({ ...scope, path: buildPath, ref: pr.merge_commit_sha });
  if (workflow.type !== 'file' || workflow.encoding !== 'base64' ||
      !Buffer.from(workflow.content, 'base64').equals(Buffer.from(trustedWorkflow))) {
    throw new Error('Candidate build workflow differs from trusted default-branch workflow');
  }
  const artifacts = await github.paginate(github.rest.actions.listWorkflowRunArtifacts, { ...scope, run_id: run.id });
  const selected = components.map(component => {
    const matches = artifacts.filter(a => a.name === `dependabot-candidate-${component}`);
    if (matches.length !== 1 || matches[0].expired || !/^sha256:[a-f0-9]{64}$/.test(matches[0].digest) ||
        !Number.isSafeInteger(matches[0].id) || matches[0].id < 1) throw new Error('Missing, ambiguous, or expired candidate artifact');
    return { component, id: matches[0].id, digest: matches[0].digest };
  });
  return { source: run.head_sha, base: pr.base.sha, run: run.id, attempt: run.run_attempt, artifacts: selected };
}

export async function publishInputs({ github, context, core }) {
  const qualified = await qualify({ github, runId: context.payload.workflow_run.id,
    expectedAttempt: context.payload.workflow_run.run_attempt,
    trustedWorkflow: await readFile(buildPath) });
  for (const name of ['source', 'base', 'run', 'attempt']) core.setOutput(name, qualified[name]);
  core.setOutput('artifacts', JSON.stringify(qualified.artifacts));
}

export async function waitForPublication({ github, source, sleep = ms => new Promise(resolve => setTimeout(resolve, ms)), maxPolls = 130 }) {
  if (!sha.test(source)) throw new Error('Invalid candidate source');
  const scope = { owner: 'Midtown-Technology-Group', repo: 'bifrost' };
  for (let poll = 0; poll < maxPolls; poll++) {
    const { data: builds } = await github.rest.actions.listWorkflowRuns({ ...scope,
      workflow_id: 'dependabot-candidates.yml', head_sha: source, event: 'pull_request', per_page: 10 });
    const producer = builds.workflow_runs[0];
    if (producer?.status === 'completed') {
      if (producer.conclusion !== 'success') throw new Error('Candidate build failed');
      const title = `Dependabot candidates ${producer.id}:${producer.run_attempt}`;
      const { data: publications } = await github.rest.actions.listWorkflowRuns({ ...scope,
        workflow_id: 'publish-dependabot-candidates.yml', event: 'workflow_run', per_page: 100 });
      const publisher = publications.workflow_runs.find(run => run.display_title === title);
      if (publisher?.status === 'completed') {
        if (publisher.conclusion !== 'success') throw new Error('Trusted candidate publication failed');
        return { producer: producer.id, publisher: publisher.id };
      }
    }
    await sleep(30000);
  }
  throw new Error('Exact signed candidate publication did not complete');
}
