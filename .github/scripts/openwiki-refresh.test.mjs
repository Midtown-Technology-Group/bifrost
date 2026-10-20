import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const testDirectory = dirname(fileURLToPath(import.meta.url));
const workflowPath = resolve(testDirectory, '../workflows/openwiki-refresh.yml');
const workflow = await readFile(workflowPath, 'utf8');
const script = extractScript(workflow);
const member = 'bifrost';
const expectedReceiver = 'https://api.github.com/repos/MTG-Thomas/bifrost-ops/dispatches';
const docsEvent = 'bifrost-docs-refresh';
const sourceEvent = 'openwiki-source-updated';
const validBefore = '1'.repeat(40);

function extractScript(source) {
  const lines = source.split(/\r?\n/);
  const marker = lines.findIndex(line => /^\s*script: \|\s*$/.test(line));
  assert.notEqual(marker, -1, 'workflow must retain an inline github-script');
  const body = [];
  for (const line of lines.slice(marker + 1)) {
    if (line.trim() && !/^\s{12}/.test(line)) break;
    body.push(line.trim() ? line.slice(12) : '');
  }
  assert.ok(body.length > 0, 'workflow inline script must not be empty');
  return body.join('\n');
}

async function invoke({ files = [], before = validBefore, commit = 'a'.repeat(40), token = 'inert-test-token', status = 204 } = {}) {
  const requests = [];
  const comparisons = [];
  const notices = [];
  const github = {
    rest: {
      repos: {
        async compareCommitsWithBasehead(args) {
          comparisons.push(args);
          return { data: { files } };
        },
      },
    },
  };
  const context = {
    payload: { before },
    sha: commit,
    repo: { owner: 'owner-under-test', repo: member },
  };
  const core = { notice: message => notices.push(message) };
  const fetchStub = async (url, options) => {
    requests.push({ url, options });
    return { status };
  };
  const processStub = { env: token === null ? { OPENWIKI_DISPATCH_TOKEN: '' } : { OPENWIKI_DISPATCH_TOKEN: token } };
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  const run = new AsyncFunction('context', 'github', 'core', 'fetch', 'process', script);
  await run(context, github, core, fetchStub, processStub);
  return { requests, comparisons, notices };
}

function eventTypes(requests) {
  return requests.map(({ options }) => JSON.parse(options.body).event_type);
}

function payloads(requests) {
  return requests.map(({ options }) => JSON.parse(options.body));
}

test('ordinary generated Markdown, Claims JSON, and exact checkpoints notify docs only', async () => {
  const { requests, comparisons } = await invoke({ files: [
    { filename: 'openwiki/index.md' },
    { filename: 'openwiki/.claims/site.json' },
    { filename: 'openwiki/.last-update.json' },
    { filename: 'openwiki/.page-manifest.json' },
  ] });
  assert.deepEqual(eventTypes(requests), [docsEvent]);
  assert.deepEqual(comparisons, [{ owner: 'owner-under-test', repo: member, basehead: `${validBefore}...${'a'.repeat(40)}` }]);
  assert.equal(requests[0].url, expectedReceiver);
  assert.deepEqual(payloads(requests), [{ event_type: docsEvent, client_payload: { member, commit: 'a'.repeat(40) } }]);
  assert.equal(requests[0].options.method, 'POST');
  assert.equal(requests[0].options.headers.Authorization, 'Bearer inert-test-token');
  assert.equal(requests[0].options.headers['X-GitHub-Api-Version'], '2022-11-28');
});

test('source edits and source-to-wiki or wiki-to-source renames notify both lanes', async () => {
  for (const file of [
    { filename: 'scripts/build.py' },
    { filename: 'openwiki/new-page.md', previous_filename: 'api/old-page.py' },
    { filename: 'api/new-page.py', previous_filename: 'openwiki/old-page.md' },
  ]) {
    const { requests } = await invoke({ files: [file] });
    assert.deepEqual(eventTypes(requests), [docsEvent, sourceEvent], JSON.stringify(file));
  }
});

test('curated INSTRUCTIONS input notifies both lanes, including either rename endpoint', async () => {
  for (const file of [
    { filename: 'openwiki/INSTRUCTIONS.md' },
    { filename: 'openwiki/INSTRUCTIONS.md', previous_filename: 'openwiki/old-instructions.md' },
    { filename: 'openwiki/new-instructions.md', previous_filename: 'openwiki/INSTRUCTIONS.md' },
  ]) {
    const { requests } = await invoke({ files: [file] });
    assert.deepEqual(eventTypes(requests), [docsEvent, sourceEvent], JSON.stringify(file));
  }
});

test('unmodeled wiki files and lookalike paths remain source changes', async () => {
  for (const filename of [
    '.openwikiignore', 'openwiki/.openwikiignore', 'openwiki/.run.json',
    'openwiki/tool.py', 'openwiki/config.yaml', 'openwiki/claims.json',
    'openwiki-other/page.md', 'docs/page.md', 'openwiki/../scripts/page.md',
  ]) {
    const { requests } = await invoke({ files: [{ filename }] });
    assert.deepEqual(eventTypes(requests), [docsEvent, sourceEvent], filename);
  }
  for (const malformed of [
    {},
    { previous_filename: 'openwiki/page.md' },
    { filename: 17, previous_filename: 'openwiki/page.md' },
    { filename: 'openwiki/page.md', previous_filename: null },
    null,
  ]) {
    const { requests } = await invoke({ files: [malformed] });
    assert.deepEqual(eventTypes(requests), [docsEvent, sourceEvent], JSON.stringify(malformed));
  }
});

test('invalid and zero before revisions fail closed without comparison', async () => {
  for (const before of ['invalid', '0'.repeat(40)]) {
    const { requests, comparisons } = await invoke({ before, files: [] });
    assert.deepEqual(eventTypes(requests), [docsEvent, sourceEvent]);
    assert.deepEqual(comparisons, []);
  }
});

test('300-file comparison cap remains conservative', async () => {
  const files = Array.from({ length: 300 }, (_, index) => ({ filename: `openwiki/page-${index}.md` }));
  const { requests } = await invoke({ files });
  assert.deepEqual(eventTypes(requests), [docsEvent, sourceEvent]);
});

test('missing token and failed dispatch retain fail-closed behavior', async () => {
  await assert.rejects(invoke({ token: null }), /OPENWIKI_DISPATCH_TOKEN is not enrolled/);
  await assert.rejects(invoke({ files: [{ filename: 'openwiki/page.md' }], status: 500 }), /Knowledge notification failed: HTTP 500/);
});

test('independent A, B, and C commits each send their own stateless notification', async () => {
  const commits = ['a', 'b', 'c'].map(char => char.repeat(40));
  const files = [
    [{ filename: 'openwiki/page-a.md' }],
    [{ filename: 'api/source-b.py' }],
    [{ filename: 'openwiki/page-c.md' }],
  ];
  const results = await Promise.all(commits.map((commit, index) => invoke({ commit, files: files[index] })));
  for (const [index, result] of results.entries()) {
    assert.deepEqual(eventTypes(result.requests), index === 1 ? [docsEvent, sourceEvent] : [docsEvent]);
    assert.ok(payloads(result.requests).every(payload => payload.client_payload.commit === commits[index]));
    assert.ok(payloads(result.requests).every(payload => payload.client_payload.member === member));
  }
  assert.doesNotMatch(workflow, /^\s*concurrency:\s*$/m, 'stateless notifications must not replace a pending run');
});
