export const repository = 'Midtown-Technology-Group/bifrost';
export const buildPath = '.github/workflows/dependabot-candidates.yml';
const sha = /^[a-f0-9]{40}$/;
const components = ['api', 'worker', 'client'];

// Both workflow_run producers execute default-branch code. PR artifacts stay data.
export async function qualify({ github, runId, expectedAttempt }) {
  const scope = { owner: 'Midtown-Technology-Group', repo: 'bifrost' };
  const { data: run } = await github.rest.actions.getWorkflowRun({ ...scope, run_id: runId });
  if (run.id !== runId || run.run_attempt !== expectedAttempt ||
      run.repository.full_name !== repository || run.event !== 'workflow_run' ||
      run.path !== buildPath || run.status !== 'completed' || run.conclusion !== 'success') {
    throw new Error('Unqualified candidate producer');
  }
  const origin = /^Dependabot candidate build ([1-9][0-9]*):([1-9][0-9]*)$/.exec(run.display_title);
  if (!origin) throw new Error('Missing original CI identity');
  const { data: ci } = await github.rest.actions.getWorkflowRun({ ...scope, run_id: Number(origin[1]) });
  if (ci.repository.full_name !== repository || ci.path !== '.github/workflows/ci.yml' ||
      ci.id !== Number(origin[1]) || ci.run_attempt !== Number(origin[2])) throw new Error('Original CI identity changed');
  if (ci.event !== 'pull_request') return null;
  if (!sha.test(ci.head_sha) || ci.pull_requests.length !== 1) throw new Error('Ambiguous CI source');
  const { data: pr } = await github.rest.pulls.get({ ...scope, pull_number: ci.pull_requests[0].number });
  if (pr.user.login !== 'dependabot[bot]') return null;
  if (pr.state !== 'open' || pr.head.repo.full_name !== repository || pr.head.sha !== ci.head_sha ||
      pr.base.ref !== 'main' || !sha.test(pr.base.sha) || pr.base.sha !== ci.pull_requests[0].base.sha) {
    throw new Error('Stale or foreign candidate source');
  }
  const artifacts = await github.paginate(github.rest.actions.listWorkflowRunArtifacts, { ...scope, run_id: run.id });
  const selected = components.map(component => {
    const matches = artifacts.filter(a => a.name === `dependabot-candidate-${component}`);
    if (matches.length !== 1 || matches[0].expired || !/^sha256:[a-f0-9]{64}$/.test(matches[0].digest) ||
        !Number.isSafeInteger(matches[0].id) || matches[0].id < 1) throw new Error('Missing, ambiguous, or expired candidate artifact');
    return { component, id: matches[0].id, digest: matches[0].digest };
  });
  return { source: ci.head_sha, base: pr.base.sha, ci: ci.id, ciAttempt: ci.run_attempt,
    run: run.id, attempt: run.run_attempt, artifacts: selected };
}

export async function publishInputs({ github, context, core }) {
  const qualified = await qualify({ github, runId: context.payload.workflow_run.id,
    expectedAttempt: context.payload.workflow_run.run_attempt });
  core.setOutput('eligible', qualified !== null ? 'true' : 'false');
  if (qualified === null) return;
  for (const name of ['source', 'base', 'run', 'attempt', 'ci', 'ciAttempt']) core.setOutput(name, qualified[name]);
  core.setOutput('artifacts', JSON.stringify(qualified.artifacts));
}

export async function waitForPublication({ github, ciRunId, ciAttempt, sleep = ms => new Promise(resolve => setTimeout(resolve, ms)), maxPolls = 130 }) {
  if (![ciRunId, ciAttempt].every(value => Number.isSafeInteger(value) && value > 0)) throw new Error('Invalid CI identity');
  const scope = { owner: 'Midtown-Technology-Group', repo: 'bifrost' };
  for (let poll = 0; poll < maxPolls; poll++) {
    const { data: builds } = await github.rest.actions.listWorkflowRuns({ ...scope,
      workflow_id: 'dependabot-candidates.yml', event: 'workflow_run', per_page: 100 });
    const producer = builds.workflow_runs.find(run => run.display_title === `Dependabot candidate build ${ciRunId}:${ciAttempt}`);
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
