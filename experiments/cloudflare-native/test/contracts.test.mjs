import test from 'node:test';
import assert from 'node:assert/strict';
import { makeCommand, parseExecutionRequest, publicStatus, InputError } from '../src/contracts.ts';
import { alice, bob, payload } from './support.mjs';

test('canonical input key order does not change identity or fingerprint', async () => {
  const first = await makeCommand(alice, 'abc', payload);
  const second = await makeCommand(alice, 'abc', { input_data: payload.input_data, workflow_id: payload.workflow_id });
  assert.deepEqual(first, second);
  assert.match(first.execution_id, /^lab_[a-f0-9]{64}$/);
  assert.ok(first.execution_id.length < 100);
});
test('identity is scoped to organization AND authenticated actor', async () => {
  const commands = await Promise.all([alice, bob, { ...alice, subject: 'another-user' }].map(
    (owner) => makeCommand(owner, 'same-key', payload),
  ));
  assert.equal(new Set(commands.map((command) => command.execution_id)).size, 3);
});
test('changed request keeps its key identity but changes fingerprint', async () => {
  const first = await makeCommand(alice, 'abc', payload);
  const second = await makeCommand(alice, 'abc', { ...payload, input_data: { targets: ['demo-c'] } });
  assert.equal(first.execution_id, second.execution_id);
  assert.notEqual(first.fingerprint, second.fingerprint);
});
for (const [name, body] of [
  ['arbitrary workflow', { ...payload, workflow_id: 'real.integration' }],
  ['organization override', { ...payload, org_id: 'victim' }],
  ['actor override', { ...payload, run_as: 'victim' }],
  ['inline code', { ...payload, code: 'run anything' }],
  ['legacy parameters alias', { workflow_id: payload.workflow_id, parameters: {} }],
  ['empty targets', { ...payload, input_data: { targets: [] } }],
  ['excessive fanout', { ...payload, input_data: { targets: Array.from({ length: 9 }, (_, n) => `demo-${n}`) } }],
  ['duplicate targets', { ...payload, input_data: { targets: ['demo-a', 'demo-a'] } }],
  ['real target', { ...payload, input_data: { targets: ['production-server'] } }],
  ['URL target', { ...payload, input_data: { targets: ['https://example.com'] } }],
  ['extra input', { ...payload, input_data: { ...payload.input_data, credentials: 'secret' } }],
]) {
  test(`rejects ${name}`, () => assert.throws(() => parseExecutionRequest(body), InputError));
}
test('missing, oversized, and non-ASCII idempotency keys are rejected', async () => {
  for (const key of [null, '', 'x'.repeat(129), 'contains space', '☁']) {
    await assert.rejects(makeCommand(alice, key, payload), InputError);
  }
});
test('request DTO is copied rather than retaining mutable input', () => {
  const input = structuredClone(payload);
  const parsed = parseExecutionRequest(input);
  input.input_data.targets.push('demo-c');
  assert.equal(parsed.input_data.targets.length, 2);
});
test('status mapping preserves unknown and paused rather than inventing success', () => {
  assert.equal(publicStatus('complete'), 'Success');
  assert.equal(publicStatus('errored'), 'Failed');
  assert.equal(publicStatus('paused'), 'Paused');
  assert.equal(publicStatus('unknown'), 'Unknown');
  assert.equal(publicStatus('new-provider-status'), 'Unknown');
  assert.equal(publicStatus('__proto__'), 'Unknown');
});
