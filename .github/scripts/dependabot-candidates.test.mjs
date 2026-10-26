import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, readFile, writeFile, chmod, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import { qualify, waitForPublication, repository, buildPath } from './dependabot-candidates.mjs';

const head = 'a'.repeat(40);
const base = 'b'.repeat(40);
const merge = 'c'.repeat(40);
const trustedWorkflow = 'trusted build instructions';

function fixture() {
  const run = { id: 101, run_attempt: 2, repository: { full_name: repository }, event: 'pull_request',
    path: buildPath, actor: { login: 'dependabot[bot]' }, status: 'completed', conclusion: 'success',
    head_sha: head, pull_requests: [{ number: 42, base: { sha: base } }] };
  const pr = { number: 42, state: 'open', user: { login: 'dependabot[bot]' },
    head: { repo: { full_name: repository }, sha: head }, base: { ref: 'main', sha: base }, merge_commit_sha: merge };
  const commit = { parents: [{ sha: base }, { sha: head }] };
  const workflow = { type: 'file', encoding: 'base64', content: Buffer.from(trustedWorkflow).toString('base64') };
  const artifacts = ['api', 'worker', 'client'].map((component, i) => ({ id: i + 1,
    name: `dependabot-candidate-${component}`, expired: false, digest: `sha256:${'d'.repeat(64)}` }));
  const calls = [];
  const respond = data => async args => { calls.push(args); return { data }; };
  const github = { rest: {
    actions: { getWorkflowRun: respond(run), listWorkflowRunArtifacts: () => {} },
    pulls: { get: respond(pr) }, repos: { getContent: respond(workflow), getCommit: respond(commit) },
  }, paginate: async () => artifacts };
  return { github, run, pr, commit, workflow, artifacts, calls };
}

const invoke = f => qualify({ github: f.github, runId: 101, expectedAttempt: 2, trustedWorkflow });

test('qualifies the exact successful same-repository bot build and immutable artifacts', async () => {
  const f = fixture();
  const result = await invoke(f);
  assert.deepEqual(result, { source: head, base, run: 101, attempt: 2,
    artifacts: f.artifacts.map(({ id, digest }, i) => ({ component: ['api', 'worker', 'client'][i], id, digest })) });
  assert.equal(f.calls.at(-1).ref, merge);
});

for (const [name, mutate] of [
  ['foreign repository', f => { f.run.repository.full_name = 'attacker/bifrost'; }],
  ['replayed producer attempt', f => { f.run.run_attempt = 3; }],
  ['foreign PR head', f => { f.pr.head.repo.full_name = 'attacker/bifrost'; }],
  ['human producer', f => { f.run.actor.login = 'human'; }],
  ['human author', f => { f.pr.user.login = 'human'; }],
  ['failed build', f => { f.run.conclusion = 'failure'; }],
  ['incomplete build', f => { f.run.status = 'in_progress'; }],
  ['wrong event', f => { f.run.event = 'pull_request_target'; }],
  ['wrong workflow', f => { f.run.path = '.github/workflows/evil.yml'; }],
  ['ambiguous PR', f => { f.run.pull_requests.push(f.run.pull_requests[0]); }],
  ['closed PR', f => { f.pr.state = 'closed'; }],
  ['stale head', f => { f.pr.head.sha = 'e'.repeat(40); }],
  ['stale base', f => { f.pr.base.sha = 'e'.repeat(40); }],
  ['non-main base', f => { f.pr.base.ref = 'other'; }],
  ['invalid source', f => { f.run.head_sha = '--upload-pack=evil'; }],
  ['missing merge', f => { f.pr.merge_commit_sha = null; }],
  ['wrong merge parents', f => { f.commit.parents.reverse(); }],
  ['modified build workflow', f => { f.workflow.content = Buffer.from('malicious code').toString('base64'); }],
  ['missing artifact', f => { f.artifacts.pop(); }],
  ['ambiguous artifact', f => { f.artifacts.push(f.artifacts[0]); }],
  ['expired artifact', f => { f.artifacts[0].expired = true; }],
  ['missing digest', f => { delete f.artifacts[0].digest; }],
  ['invalid artifact ID', f => { f.artifacts[0].id = '../evil'; }],
]) test(`rejects ${name} before admitting publication`, async () => {
  const f = fixture(); mutate(f);
  await assert.rejects(invoke(f));
});

function waiting({ buildStatus = 'completed', buildConclusion = 'success', publications = [] } = {}) {
  const calls = [];
  return { calls, github: { rest: { actions: { async listWorkflowRuns(args) {
    calls.push(args);
    return { data: { workflow_runs: args.workflow_id === 'dependabot-candidates.yml' ?
      [{ id: 101, run_attempt: 2, status: buildStatus, conclusion: buildConclusion }] : publications } };
  } } } } };
}
const publication = { id: 202, display_title: 'Dependabot candidates 101:2', status: 'completed', conclusion: 'success' };

test('waits for the exact producer attempt, never an older successful publication', async () => {
  const f = waiting({ publications: [{ ...publication, display_title: 'Dependabot candidates 101:1' }] });
  await assert.rejects(waitForPublication({ github: f.github, source: head, maxPolls: 1, sleep: async () => {} }), /did not complete/);
});
test('returns the exact completed producer and publisher receipts', async () => {
  const f = waiting({ publications: [publication] });
  assert.deepEqual(await waitForPublication({ github: f.github, source: head }), { producer: 101, publisher: 202 });
  assert.equal(f.calls[0].head_sha, head);
});
test('fails closed on producer failure', async () => {
  const f = waiting({ buildConclusion: 'failure' });
  await assert.rejects(waitForPublication({ github: f.github, source: head }), /build failed/);
});
test('fails closed on publisher failure', async () => {
  const f = waiting({ publications: [{ ...publication, conclusion: 'failure' }] });
  await assert.rejects(waitForPublication({ github: f.github, source: head }), /publication failed/);
});
test('waits only within a bounded window', async () => {
  const f = waiting({ buildStatus: 'in_progress' }); let sleeps = 0;
  await assert.rejects(waitForPublication({ github: f.github, source: head, maxPolls: 2,
    sleep: async ms => { assert.equal(ms, 30000); sleeps++; } }), /did not complete/);
  assert.equal(sleeps, 2);
});

test('privileged publisher never executes or checks out PR source; builder has no publication secrets', async () => {
  const builder = await readFile(new URL('../workflows/dependabot-candidates.yml', import.meta.url), 'utf8');
  const publisher = await readFile(new URL('../workflows/publish-dependabot-candidates.yml', import.meta.url), 'utf8');
  assert.doesNotMatch(builder, /secrets\.|packages: write|id-token: write|docker\/login-action|pull_request_target/);
  assert.match(builder, /push: false/);
  assert.match(builder, /outputs: type=oci/);
  assert.match(builder, /needs: admission/);
  assert.doesNotMatch(publisher, /pull_request_target|ref: \$\{\{.*(?:source|head_sha)|docker (?:run|build)|source .*candidate/);
  assert.match(publisher, /ref: \$\{\{ github.sha \}\}/);
  assert.match(publisher, /sha256sum --check --status/);
  assert.match(publisher, /skopeo copy --all --preserve-digests/);
  assert.match(publisher, /candidate.oci.tar/);
});

function runStep(workflow, name) {
  const body = workflow.split(`      - name: ${name}\n`)[1];
  assert.ok(body, `missing ${name}`);
  const lines = body.split('\n');
  const start = lines.indexOf('        run: |');
  assert.ok(start >= 0);
  const script = [];
  for (const line of lines.slice(start + 1)) {
    if (line.trim() && !line.startsWith('          ')) break;
    script.push(line.slice(10));
  }
  return script.join('\n');
}

for (const scenario of ['valid', 'wrong digest', 'path traversal', 'extra file']) {
  test(`artifact download boundary: ${scenario}`, async () => {
    const root = await mkdtemp(join(tmpdir(), 'bifrost-candidate-'));
    try {
      const workflow = await readFile(new URL('../workflows/publish-dependabot-candidates.yml', import.meta.url), 'utf8');
      const zip = join(root, 'fixture.zip');
      const created = spawnSync('python3', ['-c', `import sys, zipfile
with zipfile.ZipFile(sys.argv[1], 'w') as z:
    z.writestr(sys.argv[2], b'opaque OCI bytes')
    if sys.argv[3] == 'extra file': z.writestr('execute.sh', b'evil')
`, zip, scenario === 'path traversal' ? '../candidate.oci.tar' : 'candidate.oci.tar', scenario]);
      assert.equal(created.status, 0);
      const digest = createHash('sha256').update(await readFile(zip)).digest('hex');
      await writeFile(join(root, 'gh'), '#!/bin/sh\ncat "$FIXTURE_ZIP"\n');
      await chmod(join(root, 'gh'), 0o700);
      const result = spawnSync('bash', ['-c', runStep(workflow, 'Download and verify exact artifact bytes')], {
        env: { ...process.env, RUNNER_TEMP: root, FIXTURE_ZIP: zip, PATH: `${root}:${process.env.PATH}`,
          GITHUB_REPOSITORY: repository, ARTIFACT_ID: '1', ARTIFACT_DIGEST: `sha256:${scenario === 'wrong digest' ? '0'.repeat(64) : digest}` },
        encoding: 'utf8',
      });
      assert.equal(result.status === 0, scenario === 'valid', result.stderr);
      if (scenario === 'valid') assert.equal(await readFile(join(root, 'candidate.oci.tar'), 'utf8'), 'opaque OCI bytes');
      else await assert.rejects(readFile(join(root, 'candidate.oci.tar')));
    } finally { await rm(root, { recursive: true, force: true }); }
  });
}

for (const wrong of [null, 'org.opencontainers.image.revision', 'org.opencontainers.image.version',
  'org.opencontainers.image.source', 'com.midtowntg.bifrost.source-tree', 'com.midtowntg.bifrost.candidate']) {
  test(`source-label boundary rejects ${wrong ?? 'nothing for a qualified image'}`, async () => {
    const root = await mkdtemp(join(tmpdir(), 'bifrost-labels-'));
    try {
      const workflow = await readFile(new URL('../workflows/publish-dependabot-candidates.yml', import.meta.url), 'utf8');
      const labels = { 'org.opencontainers.image.source': `https://github.com/${repository}`,
        'org.opencontainers.image.revision': head, 'org.opencontainers.image.version': '3.2.2-dev.1',
        'com.midtowntg.bifrost.source-tree': merge, 'com.midtowntg.bifrost.candidate': 'true' };
      if (wrong) labels[wrong] = 'untrusted';
      await writeFile(join(root, 'labels.json'), JSON.stringify({ Labels: labels }));
      await writeFile(join(root, 'skopeo'), '#!/bin/sh\ncat "$RUNNER_TEMP/labels.json"\n');
      await chmod(join(root, 'skopeo'), 0o700);
      const result = spawnSync('bash', ['-c', runStep(workflow, 'Validate source labels before publication')], {
        env: { ...process.env, RUNNER_TEMP: root, PATH: `${root}:${process.env.PATH}`, GITHUB_REPOSITORY: repository,
          SOURCE_SHA: head, TREE_SHA: merge, VERSION: '3.2.2-dev.1' }, encoding: 'utf8',
      });
      assert.equal(result.status === 0, wrong === null, result.stderr);
    } finally { await rm(root, { recursive: true, force: true }); }
  });
}
