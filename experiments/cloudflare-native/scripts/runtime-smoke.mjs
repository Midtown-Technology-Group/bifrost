/** Runs only LOCAL Wrangler resources. Requires npm install; never logs in/deploys. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { access, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { randomUUID } from 'node:crypto';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const wrangler = join(root, 'node_modules/wrangler/bin/wrangler.js');
try { await access(wrangler); } catch {
  throw new Error('Wrangler is not installed. Run npm install before npm run test:runtime.');
}
const temporary = await mkdtemp(join(tmpdir(), 'bifrost-cf-lab-'));
const tokenA = `local-only-${randomUUID()}`;
const tokenB = `local-only-${randomUUID()}`;
const config = JSON.parse(await readFile(join(root, 'wrangler.local.jsonc'), 'utf8'));
config.main = join(root, 'src/cloudflare.ts');
config.vars = { LAB_ENABLED: 'true', LAB_PRINCIPALS: JSON.stringify({
  [tokenA]: { org_id: 'demo-org-a', subject: 'alice' },
  [tokenB]: { org_id: 'demo-org-b', subject: 'bob' },
}) };
const configPath = join(temporary, 'wrangler.json');
await writeFile(configPath, JSON.stringify(config));
const listener = createServer();
listener.listen(0, '127.0.0.1');
await once(listener, 'listening');
const port = listener.address().port;
await new Promise((resolveClose) => listener.close(resolveClose));
const base = `http://127.0.0.1:${port}`;
let child;
let logs = '';
let processError;
const childEnvironment = { ...process.env, CI: 'true', WRANGLER_SEND_METRICS: 'false' };
for (const key of Object.keys(childEnvironment)) {
  if (/^(CLOUDFLARE_|CF_)/.test(key)) delete childEnvironment[key];
}
async function start() {
  processError = undefined;
  child = spawn(process.execPath, [wrangler, 'dev', '--local', '--config', configPath,
    '--ip', '127.0.0.1', '--port', String(port), '--persist-to', join(temporary, 'state')],
  { cwd: root, env: childEnvironment, stdio: ['ignore', 'pipe', 'pipe'] });
  child.on('error', (error) => { processError = error; });
  const capture = (chunk) => { logs = (logs + chunk.toString()).slice(-16000); };
  child.stdout.on('data', capture);
  child.stderr.on('data', capture);
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    if (processError) throw processError;
    if (child.exitCode !== null) throw new Error(`Wrangler exited: ${logs}`);
    try {
      const response = await fetch(`${base}/lab/executions`, { signal: AbortSignal.timeout(1500) });
      if (response.status === 401) return;
    } catch { /* local listener is not ready yet */ }
    await sleep(200);
  }
  throw new Error(`Local Wrangler startup timed out: ${logs}`);
}
async function stop() {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const stopped = once(child, 'exit');
  child.kill('SIGTERM');
  const killer = setTimeout(() => child.kill('SIGKILL'), 5000);
  try { await stopped; } finally { clearTimeout(killer); }
}
function submit(targets = ['demo-a', 'demo-b'], token = tokenA) {
  return fetch(`${base}/lab/executions`, { method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', 'Idempotency-Key': 'smoke-v1' },
    body: JSON.stringify({ workflow_id: 'lab.inventory.v1', input_data: { targets } }),
    signal: AbortSignal.timeout(15000),
  });
}
function inspect(path, token = tokenA) {
  return fetch(`${base}${path}`, { headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(5000) });
}
async function completed(path, token = tokenA) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const response = await inspect(path, token);
    assert.equal(response.status, 200);
    const state = await response.json();
    if (state.status === 'Success') return state;
    assert.notEqual(state.status, 'Failed');
    await sleep(200);
  }
  throw new Error('Local workflow did not complete');
}
try {
  await start();
  const responses = await Promise.all([submit(), submit()]);
  responses.forEach((response) => assert.equal(response.status, 202));
  const [first, duplicate] = await Promise.all(responses.map((response) => response.json()));
  assert.equal(first.execution_id, duplicate.execution_id);
  assert.equal((await submit(['demo-c'])).status, 409);
  assert.equal((await inspect(first.status_url, tokenB)).status, 404);
  const other = await (await submit(undefined, tokenB)).json();
  assert.notEqual(first.execution_id, other.execution_id);
  const done = await completed(first.status_url);
  assert.equal(done.result.results.length, 2);
  assert.ok(done.result.results.every((result) => result.simulated));
  await completed(other.status_url, tokenB);
  await stop();
  await start();
  const replayResponse = await submit();
  assert.equal(replayResponse.status, 202);
  const replay = await replayResponse.json();
  assert.equal(replay.execution_id, first.execution_id);
  assert.equal(replay.replayed, true);
  const restored = await completed(first.status_url);
  assert.deepEqual(restored.result, done.result);
  assert.equal((await inspect(first.status_url, tokenB)).status, 404);
  console.log('PASS: local bindings, Workflow execution, duplicate/conflict admission, tenant isolation, and restart persistence');
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await stop();
  await rm(temporary, { recursive: true, force: true });
}
