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

function fixture() {
  const run = { id: 101, run_attempt: 2, repository: { full_name: repository }, event: 'workflow_run',
    path: buildPath, status: 'completed', conclusion: 'success', display_title: 'Dependabot candidate build 50:1' };
  const ci = { id: 50, run_attempt: 1, repository: { full_name: repository }, event: 'pull_request',
    path: '.github/workflows/ci.yml', head_sha: head, pull_requests: [{ number: 42, base: { sha: base } }] };
  const pr = { number: 42, state: 'open', user: { login: 'dependabot[bot]' },
    head: { repo: { full_name: repository }, sha: head }, base: { ref: 'main', sha: base } };
  const artifacts = ['api', 'worker', 'client'].map((component, i) => ({ id: i + 1,
    name: `dependabot-candidate-${component}`, expired: false, digest: `sha256:${'d'.repeat(64)}` }));
  const calls = [];
  const respond = data => async args => { calls.push(args); return { data }; };
  const github = { rest: {
    actions: { getWorkflowRun: async args => { calls.push(args); return { data: args.run_id === 101 ? run : ci }; }, listWorkflowRunArtifacts: () => {} },
    pulls: { get: respond(pr) },
  }, paginate: async () => artifacts };
  return { github, run, ci, pr, artifacts, calls };
}

const invoke = f => qualify({ github: f.github, runId: 101, expectedAttempt: 2 });

test('qualifies the exact successful same-repository bot build and immutable artifacts', async () => {
  const f = fixture();
  const result = await invoke(f);
  assert.deepEqual(result, { source: head, base, ci: 50, ciAttempt: 1, run: 101, attempt: 2,
    artifacts: f.artifacts.map(({ id, digest }, i) => ({ component: ['api', 'worker', 'client'][i], id, digest })) });
  assert.equal(f.calls[1].run_id, 50);
});

for (const [name, mutate] of [
  ['foreign repository', f => { f.run.repository.full_name = 'attacker/bifrost'; }],
  ['replayed producer attempt', f => { f.run.run_attempt = 3; }],
  ['foreign PR head', f => { f.pr.head.repo.full_name = 'attacker/bifrost'; }],
  ['failed build', f => { f.run.conclusion = 'failure'; }],
  ['incomplete build', f => { f.run.status = 'in_progress'; }],
  ['wrong event', f => { f.run.event = 'pull_request_target'; }],
  ['wrong workflow', f => { f.run.path = '.github/workflows/evil.yml'; }],
  ['ambiguous PR', f => { f.ci.pull_requests.push(f.ci.pull_requests[0]); }],
  ['closed PR', f => { f.pr.state = 'closed'; }],
  ['stale head', f => { f.pr.head.sha = 'e'.repeat(40); }],
  ['stale base', f => { f.pr.base.sha = 'e'.repeat(40); }],
  ['non-main base', f => { f.pr.base.ref = 'other'; }],
  ['invalid source', f => { f.ci.head_sha = '--upload-pack=evil'; }],
  ['missing original CI identity', f => { f.run.display_title = 'untrusted'; }],
  ['wrong original CI workflow', f => { f.ci.path = '.github/workflows/evil.yml'; }],
  ['replayed CI attempt', f => { f.ci.run_attempt = 2; }],
  ['foreign original CI repository', f => { f.ci.repository.full_name = 'attacker/bifrost'; }],
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
  const github = { rest: { actions: {
    async getWorkflowRun() { return { data: { id: 50, run_attempt: 1, created_at: '2026-10-26T00:00:00Z' } }; },
    async listWorkflowRuns(args) {
    calls.push(args);
    return { data: { workflow_runs: args.workflow_id === 'dependabot-candidates.yml' ?
      [{ id: 101, run_attempt: 2, display_title: 'Dependabot candidate build 50:1', status: buildStatus, conclusion: buildConclusion }] : publications } };
  } } } };
  github.paginate = async (method, args) => (await method(args)).data.workflow_runs;
  return { calls, github };
}
const publication = { id: 202, display_title: 'Dependabot candidates 101:2', status: 'completed', conclusion: 'success' };

test('waits for the exact producer attempt, never an older successful publication', async () => {
  const f = waiting({ publications: [{ ...publication, display_title: 'Dependabot candidates 101:1' }] });
  await assert.rejects(waitForPublication({ github: f.github, ciRunId: 50, ciAttempt: 1, maxPolls: 1, sleep: async () => {} }), /did not complete/);
});
test('returns the exact completed producer and publisher receipts', async () => {
  const f = waiting({ publications: [publication] });
  assert.deepEqual(await waitForPublication({ github: f.github, ciRunId: 50, ciAttempt: 1 }), { producer: 101, publisher: 202 });
  assert.equal(f.calls[0].event, 'workflow_run');
});
test('fails closed on producer failure', async () => {
  const f = waiting({ buildConclusion: 'failure' });
  await assert.rejects(waitForPublication({ github: f.github, ciRunId: 50, ciAttempt: 1 }), /build failed/);
});
test('fails closed on publisher failure', async () => {
  const f = waiting({ publications: [{ ...publication, conclusion: 'failure' }] });
  await assert.rejects(waitForPublication({ github: f.github, ciRunId: 50, ciAttempt: 1 }), /publication failed/);
});
test('waits only within a bounded window', async () => {
  const f = waiting({ buildStatus: 'in_progress' }); let sleeps = 0;
  await assert.rejects(waitForPublication({ github: f.github, ciRunId: 50, ciAttempt: 1, maxPolls: 2,
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

for (const scenario of ['success', 'human PR', 'non-PR CI', 'lint failure', 'stale head', 'changed attempt']) {
  test(`default-branch build admission: ${scenario}`, async () => {
    const f = fixture();
    if (scenario === 'human PR') f.pr.user.login = 'human';
    if (scenario === 'non-PR CI') f.ci.event = 'workflow_dispatch';
    if (scenario === 'stale head') f.pr.head.sha = 'e'.repeat(40);
    if (scenario === 'changed attempt') f.ci.run_attempt = 2;
    if (scenario === 'non-PR CI') { assert.equal(await invoke(f), null); return; }
    const workflow = await readFile(new URL('../workflows/dependabot-candidates.yml', import.meta.url), 'utf8');
    const body = workflow.split('          script: |\n')[1].split('  build:')[0];
    const script = body.split('\n').map(line => line.slice(12)).join('\n');
    const outputs = {};
    const source = { ...f.ci, run_attempt: 1 };
    f.github.paginate = async () => [{ name: 'Lint & Type Check', status: 'completed',
      conclusion: scenario === 'lint failure' ? 'failure' : 'success' }];
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    const invokeAdmission = () => new AsyncFunction('github', 'context', 'core', script)(f.github,
      { payload: { workflow_run: source, repository: { full_name: repository } },
        repo: { owner: 'Midtown-Technology-Group', repo: 'bifrost' } },
      { setOutput: (name, value) => { outputs[name] = value; } });
    if (['lint failure', 'stale head', 'changed attempt'].includes(scenario)) {
      await assert.rejects(invokeAdmission());
      assert.equal(outputs.eligible, undefined);
    } else {
      await invokeAdmission();
      if (scenario === 'human PR') {
        assert.deepEqual(outputs, { eligible: 'false' });
        assert.equal(await invoke(f), null);
      } else assert.deepEqual(outputs, { eligible: 'true', source: head, base, pr: 42 });
    }
  });
}

for (const [login, userId, allowed] of [
  ['MTG-Thomas', 87775189, true], ['Other-Principal', 123, true],
  ['owner\ninjected=evil', 1, false], ['owner', 0, false], ['owner', true, false],
]) {
  test(`credential principal readback validates output: ${JSON.stringify(login)} / ${userId}`, async () => {
    const root = await mkdtemp(join(tmpdir(), 'bifrost-principal-'));
    try {
      const workflow = await readFile(new URL('../workflows/publish-dependabot-candidates.yml', import.meta.url), 'utf8');
      const sentinel = 'private-sentinel-do-not-log';
      await writeFile(join(root, 'profile.json'), JSON.stringify({ login, id: userId }));
      await writeFile(join(root, 'sitecustomize.py'), `import io, os, pathlib, urllib.request
def response(request, timeout):
    assert request.full_url == 'https://api.github.com/user'
    assert request.get_method() == 'GET'
    assert request.get_header('Authorization') == 'Bearer ' + os.environ['GHCR_TOKEN']
    assert timeout == 30
    return io.StringIO(pathlib.Path(os.environ['PROFILE']).read_text())
urllib.request.urlopen = response
`);
      const output = join(root, 'output');
      const result = spawnSync('bash', ['-c', runStep(workflow, 'Read existing credential principal')], {
        env: { ...process.env, PYTHONPATH: root, PROFILE: join(root, 'profile.json'),
          GHCR_TOKEN: sentinel, GITHUB_OUTPUT: output }, encoding: 'utf8',
      });
      assert.equal(result.status === 0, allowed, result.stderr);
      assert.ok(!`${result.stdout}${result.stderr}`.includes(sentinel));
      if (allowed) assert.equal(await readFile(output, 'utf8'), `login=${login}\n`);
      else await assert.rejects(readFile(output));
    } finally { await rm(root, { recursive: true, force: true }); }
  });
}

test('publication lookup paginates both workflow histories within the exact CI time window', async () => {
  const f = waiting({ publications: [publication] });
  let pages = 0;
  f.github.paginate = async (method, args) => {
    pages++;
    assert.equal(args.created, '>=2026-10-26T00:00:00Z');
    const first = Array.from({ length: 100 }, () => ({ display_title: 'unrelated', status: 'completed' }));
    return [...first, ...(await method(args)).data.workflow_runs];
  };
  assert.deepEqual(await waitForPublication({ github: f.github, ciRunId: 50, ciAttempt: 1 }), { producer: 101, publisher: 202 });
  assert.equal(pages, 2);
});

test('publication lookup rejects a superseded CI attempt', async () => {
  const f = waiting();
  f.github.rest.actions.getWorkflowRun = async () => ({ data: { id: 50, run_attempt: 2, created_at: '2026-10-26T00:00:00Z' } });
  await assert.rejects(waitForPublication({ github: f.github, ciRunId: 50, ciAttempt: 1 }), /identity changed/);
});
