import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, readFile, writeFile, chmod, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import { qualify, waitForPublication, repository, buildPath, selectArtifacts } from './dependabot-candidates.mjs';

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
    name: `dependabot-candidate-${component}-attempt-2`, expired: false, digest: `sha256:${'d'.repeat(64)}` }));
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
    artifacts: f.artifacts.map(({ id, digest }, i) => ({ component: ['api', 'worker', 'client'][i], id, digest, attempt: 2 })) });
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
    async listWorkflowRunArtifacts() { return { data: { artifacts: publicationArtifacts } }; },
    async listWorkflowRuns(args) {
    calls.push(args);
    return { data: { workflow_runs: args.workflow_id === 'dependabot-candidates.yml' ?
      [{ id: 101, run_attempt: 2, display_title: 'Dependabot candidate build 50:1', status: buildStatus, conclusion: buildConclusion }] : publications } };
  } } } };
  github.paginate = async (method, args) => { const { data } = await method(args); return data.workflow_runs ?? data.artifacts; };
  return { calls, github };
}
const publicationArtifacts = ['api', 'worker', 'client'].map((component, i) => ({
  name: `dependabot-publication-${component}-attempt-1`, id: i + 10, expired: false, digest: `sha256:${'e'.repeat(64)}`,
}));
const publicationReceipts = publicationArtifacts.map(({ id, digest }, i) => ({ component: ['api', 'worker', 'client'][i], id, digest, attempt: 1 }));
const publication = { id: 202, run_attempt: 1, display_title: 'Dependabot candidates 101:2', status: 'completed', conclusion: 'success' };

test('waits for the exact producer attempt, never an older successful publication', async () => {
  const f = waiting({ publications: [{ ...publication, display_title: 'Dependabot candidates 101:1' }] });
  await assert.rejects(waitForPublication({ github: f.github, ciRunId: 50, ciAttempt: 1, maxPolls: 1, sleep: async () => {} }), /did not complete/);
});
test('returns the exact completed producer and publisher receipts', async () => {
  const f = waiting({ publications: [publication] });
  assert.deepEqual(await waitForPublication({ github: f.github, ciRunId: 50, ciAttempt: 1 }), { producer: 101, publisher: 202, publisherAttempt: 1, artifacts: publicationReceipts });
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
    if (args.workflow_id) assert.equal(args.created, '>=2026-10-26T00:00:00Z');
    const first = Array.from({ length: 100 }, () => ({ display_title: 'unrelated', status: 'completed' }));
    const { data } = await method(args);
    return [...first, ...(data.workflow_runs ?? data.artifacts)];
  };
  assert.deepEqual(await waitForPublication({ github: f.github, ciRunId: 50, ciAttempt: 1 }), { producer: 101, publisher: 202, publisherAttempt: 1, artifacts: publicationReceipts });
  assert.equal(pages, 3);
});

test('publication lookup rejects a superseded CI attempt', async () => {
  const f = waiting();
  f.github.rest.actions.getWorkflowRun = async () => ({ data: { id: 50, run_attempt: 2, created_at: '2026-10-26T00:00:00Z' } });
  await assert.rejects(waitForPublication({ github: f.github, ciRunId: 50, ciAttempt: 1 }), /identity changed/);
});

for (const scenario of ['missing', 'duplicate', 'expired', 'invalid hash']) {
  test(`publication receipts reject ${scenario}`, () => {
    const artifacts = structuredClone(publicationArtifacts);
    if (scenario === 'missing') artifacts.pop();
    if (scenario === 'duplicate') artifacts.push(artifacts[0]);
    if (scenario === 'expired') artifacts[0].expired = true;
    if (scenario === 'invalid hash') artifacts[0].digest = 'untrusted';
    assert.throws(() => selectArtifacts(artifacts, 'dependabot-publication', 1));
  });
}

for (const scenario of ['valid', 'wrong archive hash', 'wrong image digest', 'extra member', 'missing component', 'mixed attempt']) {
  test(`CI consumes exact publisher digest receipts: ${scenario}`, async () => {
    const root = await mkdtemp(join(tmpdir(), 'bifrost-digests-'));
    try {
      const workflow = await readFile(new URL('../workflows/ci.yml', import.meta.url), 'utf8');
      const imageDigest = `sha256:${'f'.repeat(64)}`;
      const artifacts = [];
      for (const [i, component] of ['api', 'worker', 'client'].entries()) {
        const zip = join(root, `${i + 1}.zip`);
        const created = spawnSync('python3', ['-c', `import sys, zipfile
with zipfile.ZipFile(sys.argv[1], 'w') as z:
    z.writestr('digest', sys.argv[2])
    if sys.argv[3] == 'extra member': z.writestr('../other', 'evil')
`, zip, scenario === 'wrong image digest' ? 'untrusted' : imageDigest, scenario]);
        assert.equal(created.status, 0);
        artifacts.push({ component, attempt: 1, id: i + 1, digest: `sha256:${createHash('sha256').update(await readFile(zip)).digest('hex')}` });
      }
      if (scenario === 'wrong archive hash') artifacts[0].digest = `sha256:${'0'.repeat(64)}`;
      if (scenario === 'missing component') artifacts.pop();
      if (scenario === 'mixed attempt') artifacts[0].attempt = 2;
      await writeFile(join(root, 'gh'), '#!/bin/sh\nartifact="${2%/zip}"\nartifact="${artifact##*/}"\ncat "$FIXTURE_ROOT/$artifact.zip"\n');
      await chmod(join(root, 'gh'), 0o700);
      const output = join(root, 'outputs');
      const result = spawnSync('bash', ['-c', runStep(workflow, 'Read exact publication digests')], {
        env: { ...process.env, FIXTURE_ROOT: root, PATH: `${root}:${process.env.PATH}`, GITHUB_OUTPUT: output,
          GITHUB_REPOSITORY: repository, PUBLICATION_ARTIFACTS: JSON.stringify(artifacts), PUBLICATION_ATTEMPT: '1' }, encoding: 'utf8',
      });
      assert.equal(result.status === 0, scenario === 'valid', result.stderr);
      if (scenario === 'valid') assert.equal(await readFile(output, 'utf8'), ['api', 'worker', 'client'].map(c => `${c}_digest=${imageDigest}\n`).join(''));
      else await assert.rejects(readFile(output));
    } finally { await rm(root, { recursive: true, force: true }); }
  });
}

test('builder executes only the retained trusted version script, outside PR context', async () => {
  const root = await mkdtemp(join(tmpdir(), 'bifrost-version-'));
  try {
    const { mkdir } = await import('node:fs/promises');
    await mkdir(join(root, 'trusted-version/scripts'), { recursive: true });
    await mkdir(join(root, 'scripts'));
    await writeFile(join(root, 'trusted-version/scripts/compute-dev-version.sh'), '#!/bin/sh\nprintf "3.2.2-dev.1\\n"\n');
    await chmod(join(root, 'trusted-version/scripts/compute-dev-version.sh'), 0o700);
    await writeFile(join(root, 'scripts/compute-dev-version.sh'), '#!/bin/sh\ntouch "$RUNNER_TEMP/pr-script-executed"\nexit 1\n');
    await chmod(join(root, 'scripts/compute-dev-version.sh'), 0o700);
    await writeFile(join(root, 'git'), `#!/bin/sh
if [ "$1" = '-C' ]; then exit 0; fi
if [ "$2" = 'HEAD' ]; then printf '${head}\\n'; else printf '${merge}\\n'; fi
`);
    await chmod(join(root, 'git'), 0o700);
    const workflow = await readFile(new URL('../workflows/dependabot-candidates.yml', import.meta.url), 'utf8');
    assert.ok(workflow.indexOf('Retain trusted version tooling') < workflow.indexOf('Checkout exact pull-request head'));
    const result = spawnSync('bash', ['-c', runStep(workflow, 'Compute immutable candidate identity')], {
      cwd: root, env: { ...process.env, RUNNER_TEMP: root, PATH: `${root}:${process.env.PATH}`, HEAD_SHA: head,
        BASE_SHA: base, PR_NUMBER: '42', GITHUB_OUTPUT: join(root, 'output') }, encoding: 'utf8',
    });
    assert.equal(result.status, 0, result.stderr);
    await assert.rejects(readFile(join(root, 'pr-script-executed')));
    assert.match(await readFile(join(root, 'output'), 'utf8'), /version=3.2.2-dev.1/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

for (const prefix of ['dependabot-candidate', 'dependabot-publication']) {
  const artifactsFor = (attempt, offset) => ['api', 'worker', 'client'].map((component, i) => ({
    name: `${prefix}-${component}-attempt-${attempt}`, id: offset + i, expired: false, digest: `sha256:${'d'.repeat(64)}`,
  }));
  test(`${prefix}: rerun-all selects a coherent fresh set and ignores earlier-attempt duplicates`, () => {
    const old = artifactsFor(1, 10);
    const current = artifactsFor(2, 20);
    const selected = selectArtifacts([...old, ...current], prefix, 2);
    assert.deepEqual(selected.map(a => [a.id, a.attempt]), [[20, 2], [21, 2], [22, 2]]);
  });
  test(`${prefix}: failed-legs-only rerun cannot mix earlier successful legs with current artifacts`, () => {
    const old = artifactsFor(1, 10);
    const current = artifactsFor(2, 20);
    assert.throws(() => selectArtifacts([old[0], old[1], current[2]], prefix, 2), /Missing/);
  });
}

test('qualification rejects a successful partial rerun whose component artifacts mix attempts', async () => {
  const f = fixture();
  f.artifacts[0].name = 'dependabot-candidate-api-attempt-1';
  await assert.rejects(invoke(f), /Missing/);
});

test('publication lookup rejects mixed-attempt receipts even when the latest publisher run succeeded', async () => {
  const f = waiting({ publications: [{ ...publication, run_attempt: 2 }] });
  const artifacts = structuredClone(publicationArtifacts);
  artifacts[2].name = 'dependabot-publication-client-attempt-2';
  f.github.rest.actions.listWorkflowRunArtifacts = async () => ({ data: { artifacts } });
  await assert.rejects(waitForPublication({ github: f.github, ciRunId: 50, ciAttempt: 1 }), /Missing/);
});

test('both artifact producers name every component with their own exact run attempt', async () => {
  const builder = await readFile(new URL('../workflows/dependabot-candidates.yml', import.meta.url), 'utf8');
  const publisher = await readFile(new URL('../workflows/publish-dependabot-candidates.yml', import.meta.url), 'utf8');
  assert.ok(builder.includes('name: dependabot-candidate-${{ matrix.component }}-attempt-${{ github.run_attempt }}'));
  assert.ok(publisher.includes('name: dependabot-publication-${{ matrix.artifact.component }}-attempt-${{ github.run_attempt }}'));
});
