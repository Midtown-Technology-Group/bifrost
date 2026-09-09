import test from 'node:test';
import assert from 'node:assert/strict';
import { handleRequest } from '../src/http.ts';
import { MAX_BODY_BYTES } from '../src/contracts.ts';
import { system, post, get, config, payload, tokenA, tokenB, alice } from './support.mjs';

test('HTTP create, replay, status, and synthetic completion', async () => {
  const s = system();
  const response = await handleRequest(post(), config, s.api);
  assert.equal(response.status, 202);
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  const accepted = await response.json();
  assert.equal(response.headers.get('Location'), accepted.status_url);
  const replay = await (await handleRequest(post(), config, s.api)).json();
  assert.equal(replay.execution_id, accepted.execution_id);
  assert.equal(replay.replayed, true);
  await s.workflows.complete(accepted.execution_id);
  const status = await (await handleRequest(get(accepted.execution_id), config, s.api)).json();
  assert.equal(status.status, 'Success');
  assert.equal(status.result.results.length, 2);
  assert.ok(status.result.results.every((result) => result.simulated));
});
test('no authentication or wrong token never reaches admission', async () => {
  const s = system();
  for (const token of [null, 'wrong-token']) {
    assert.equal((await handleRequest(post(payload, 'key', token), config, s.api)).status, 401);
  }
  assert.equal(s.stores.size, 0);
});
test('disabled, missing, empty, or malformed fixture config fails closed', async () => {
  const s = system();
  for (const env of [{}, { ...config, LAB_ENABLED: 'false' },
    { ...config, LAB_PRINCIPALS: '' }, { ...config, LAB_PRINCIPALS: '{}' },
    { ...config, LAB_PRINCIPALS: '{bad-json' }]) {
    assert.equal((await handleRequest(post(), env, s.api)).status, 503);
  }
  assert.equal(s.stores.size, 0);
});
test('client organization headers do not override the authenticated principal', async () => {
  const s = system();
  await handleRequest(post(payload, 'key', tokenA, { 'X-Org-Id': 'victim' }), config, s.api);
  assert.deepEqual([...s.stores.values()][0].state.command.owner, alice);
});
test('different authenticated organizations get different runs and cannot read each other', async () => {
  const s = system();
  const a = await (await handleRequest(post(payload, 'same-key', tokenA), config, s.api)).json();
  const b = await (await handleRequest(post(payload, 'same-key', tokenB), config, s.api)).json();
  assert.notEqual(a.execution_id, b.execution_id);
  assert.equal((await handleRequest(get(a.execution_id, tokenB), config, s.api)).status, 404);
});
test('conflicting replay returns HTTP 409', async () => {
  const s = system();
  await handleRequest(post(), config, s.api);
  const changed = { ...payload, input_data: { targets: ['demo-c'] } };
  assert.equal((await handleRequest(post(changed), config, s.api)).status, 409);
});
test('missing key, invalid JSON, and unsupported inputs return 422', async () => {
  const s = system();
  for (const request of [post(payload, null), post('{bad-json'), post({ ...payload, run_as: 'victim' })]) {
    assert.equal((await handleRequest(request, config, s.api)).status, 422);
  }
  assert.equal(s.stores.size, 0);
});
test('rejects wrong content type', async () => {
  const s = system();
  assert.equal((await handleRequest(post(payload, 'key', tokenA, { 'Content-Type': 'text/plain' }), config, s.api)).status, 415);
});
test('body limits apply even without Content-Length', async () => {
  const s = system();
  const response = await handleRequest(post(' '.repeat(MAX_BODY_BYTES + 1)), config, s.api);
  assert.equal(response.status, 413);
  assert.equal(s.stores.size, 0);
});
test('declared oversize body is rejected before admission', async () => {
  const s = system();
  assert.equal((await handleRequest(post(payload, 'key', tokenA, {
    'Content-Length': String(MAX_BODY_BYTES + 1),
  }), config, s.api)).status, 413);
});
test('provider failure returns retryable 503, not 202, and does not leak errors', async () => {
  const api = { submit: async () => { throw new Error('SECRET: vendor-token'); } };
  const response = await handleRequest(post(), config, api);
  assert.equal(response.status, 503);
  assert.equal(response.headers.get('Retry-After'), '5');
  assert.ok(!(await response.text()).includes('vendor-token'));
});
test('expired ambiguous launch exposes its lookup path without launching a new run', async () => {
  const response = await handleRequest(post(), config, { submit: async () => ({ kind: 'expired' }) });
  assert.equal(response.status, 409);
  assert.match((await response.json()).status_url, /^\/lab\/executions\/lab_/);
});
test('provider error details are not exposed in status', async () => {
  const s = system();
  const accepted = await (await handleRequest(post(), config, s.api)).json();
  s.api.inspect = async () => ({ kind: 'found', runtime: { status: 'errored', error: { message: 'secret' } } });
  const response = await handleRequest(get(accepted.execution_id), config, s.api);
  assert.equal(response.status, 200);
  assert.ok(!(await response.text()).includes('secret'));
});
test('unsupported methods and IDs do not mutate anything', async () => {
  const s = system();
  assert.equal((await handleRequest(get('not-an-execution'), config, s.api)).status, 404);
  assert.equal(s.stores.size, 0);
});
