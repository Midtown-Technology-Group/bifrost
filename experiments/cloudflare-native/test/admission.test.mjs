import test from 'node:test';
import assert from 'node:assert/strict';
import { AdmissionService } from '../src/admission.ts';
import { ADMISSION_RETRY_WINDOW_MS, makeCommand } from '../src/contracts.ts';
import { AtomicStore, FakeWorkflows, alice, bob, payload } from './support.mjs';

async function fixture(now) {
  const command = await makeCommand(alice, 'test-key', payload);
  const store = new AtomicStore();
  const workflows = new FakeWorkflows();
  const service = new AdmissionService(store, workflows, now);
  return { command, store, workflows, service };
}
test('admission is durable before launch and returns accepted only after launch', async () => {
  const f = await fixture();
  const original = f.workflows.create.bind(f.workflows);
  f.workflows.create = async (command) => {
    assert.deepEqual((await f.store.read()).command, command);
    await original(command);
  };
  assert.equal((await f.service.submit(f.command)).kind, 'accepted');
  assert.equal((await f.store.read()).started, true);
});
test('concurrent duplicate submits create only one logical instance', async () => {
  const f = await fixture();
  const results = await Promise.all(Array.from({ length: 20 }, () => f.service.submit(f.command)));
  assert.ok(results.every((result) => result.kind === 'accepted'));
  assert.equal(f.workflows.instances.size, 1);
  assert.equal(results.filter((result) => !result.replayed).length, 1);
});
test('same key with a different payload conflicts without changing durable input', async () => {
  const f = await fixture();
  const other = await makeCommand(alice, 'test-key', { ...payload, input_data: { targets: ['demo-c'] } });
  await f.service.submit(f.command);
  assert.equal((await f.service.submit(other)).kind, 'conflict');
  assert.deepEqual((await f.store.read()).command, f.command);
  assert.equal(f.workflows.createCalls, 1);
});
test('conflicting concurrent requests do not both launch', async () => {
  const f = await fixture();
  const other = await makeCommand(alice, 'test-key', { ...payload, input_data: { targets: ['demo-c'] } });
  const results = await Promise.all([f.service.submit(f.command), f.service.submit(other)]);
  assert.deepEqual(results.map((result) => result.kind).sort(), ['accepted', 'conflict']);
  assert.equal(f.workflows.instances.size, 1);
});
test('lost successful create response is recovered by confirmed status', async () => {
  const f = await fixture();
  f.workflows.loseCreateResponse = true;
  assert.equal((await f.service.submit(f.command)).kind, 'accepted');
  assert.equal(f.workflows.statusCalls, 1);
});
test('quota/provider errors are not treated as duplicate success', async () => {
  const f = await fixture();
  f.workflows.unavailable = true;
  await assert.rejects(f.service.submit(f.command));
  assert.equal((await f.store.read()).started, false);
  f.workflows.unavailable = false;
  assert.equal((await f.service.submit(f.command)).kind, 'accepted');
  assert.equal(f.workflows.instances.size, 1);
});
test('unknown status is not proof of successful creation', async () => {
  const f = await fixture();
  f.workflows.create = async () => { throw new Error('ambiguous'); };
  f.workflows.status = async () => ({ status: 'unknown' });
  await assert.rejects(f.service.submit(f.command), /could not be confirmed/);
  assert.equal((await f.store.read()).started, false);
});
test('receipt write failure is recovered after reconstructing the service', async () => {
  const f = await fixture();
  f.store.failReceipt = true;
  await assert.rejects(f.service.submit(f.command), /receipt write failure/);
  const reconstructed = new AdmissionService(f.store, f.workflows);
  assert.equal((await reconstructed.submit(f.command)).kind, 'accepted');
  assert.equal(f.workflows.instances.size, 1);
});
test('foreign organizations and same-org foreign actors cannot inspect a run', async () => {
  const f = await fixture();
  await f.service.submit(f.command);
  for (const owner of [bob, { ...alice, subject: 'another-user' }]) {
    assert.deepEqual(await f.service.inspect(owner, f.command.execution_id), { kind: 'not_found' });
  }
  assert.equal(f.workflows.statusCalls, 0);
});
test('retained admission receipt prevents restart after Workflow history is removed', async () => {
  const f = await fixture();
  await f.service.submit(f.command);
  f.workflows.instances.clear();
  assert.equal((await f.service.submit(f.command)).kind, 'accepted');
  assert.equal(f.workflows.createCalls, 1);
  await assert.rejects(f.service.inspect(alice, f.command.execution_id), /not found/);
});
test('old ambiguous admissions expire rather than silently being restarted', async () => {
  let now = 0;
  const f = await fixture(() => now);
  f.workflows.unavailable = true;
  await assert.rejects(f.service.submit(f.command));
  now = ADMISSION_RETRY_WINDOW_MS;
  f.workflows.unavailable = false;
  assert.deepEqual(await f.service.submit(f.command), { kind: 'expired' });
  assert.equal(f.workflows.createCalls, 1);
});
test('missing admission and mismatched execution IDs are private', async () => {
  const f = await fixture();
  assert.deepEqual(await f.service.inspect(alice, f.command.execution_id), { kind: 'not_found' });
  await f.service.submit(f.command);
  assert.deepEqual(await f.service.inspect(alice, 'wrong-id'), { kind: 'not_found' });
});
