import test from 'node:test';
import assert from 'node:assert/strict';
import { runInventory, syntheticExecutor } from '../src/orchestration.ts';
import { makeCommand } from '../src/contracts.ts';
import { alice, payload } from './support.mjs';

test('each target has a stable, versioned step and operation ID', async () => {
  const command = await makeCommand(alice, 'key', payload);
  const names = [];
  const result = await runInventory(command, { run: async (name, action) => { names.push(name); return action(); } });
  assert.deepEqual(names, ['inventory-v1-0', 'inventory-v1-1']);
  assert.equal(new Set(result.results.map((row) => row.operation_id)).size, 2);
  assert.deepEqual(result.results.map((row) => row.target), payload.input_data.targets);
});
test('a step retry reuses its operation ID rather than inventing a new command', async () => {
  const command = await makeCommand(alice, 'key', payload);
  const calls = [];
  let first = true;
  const executor = { execute: async (input) => {
    calls.push(input);
    if (first) { first = false; throw new Error('transient failure'); }
    return syntheticExecutor.execute(input);
  } };
  const steps = { run: async (_name, action) => {
    try { return await action(); } catch { return action(); }
  } };
  await runInventory(command, steps, executor);
  assert.equal(calls[0].operation_id, calls[1].operation_id);
  assert.deepEqual(calls[0].owner, alice);
});
test('checkpoint replay avoids completed steps in a test step-cache double', async () => {
  const command = await makeCommand(alice, 'key', payload);
  const cache = new Map();
  let calls = 0;
  const executor = { execute: async (input) => { calls++; return syntheticExecutor.execute(input); } };
  const steps = { run: async (name, action) => {
    if (!cache.has(name)) cache.set(name, await action());
    return structuredClone(cache.get(name));
  } };
  const first = await runInventory(command, steps, executor);
  assert.deepEqual(await runInventory(command, steps, executor), first);
  assert.equal(calls, 2);
});
test('lost step checkpoint repeats delivery: downstream idempotency is required', async () => {
  const command = await makeCommand(alice, 'key', payload);
  const receipts = new Map();
  let deliveries = 0;
  let effects = 0;
  const executor = { execute: async (input) => {
    deliveries++;
    if (!receipts.has(input.operation_id)) {
      effects++;
      receipts.set(input.operation_id, await syntheticExecutor.execute(input));
    }
    return receipts.get(input.operation_id);
  } };
  const steps = { run: async (_name, action) => { await action(); return action(); } };
  await runInventory(command, steps, executor);
  assert.equal(deliveries, 4);
  assert.equal(effects, 2);
});
test('permanent failure is propagated instead of reported as success', async () => {
  const command = await makeCommand(alice, 'key', payload);
  await assert.rejects(runInventory(command, { run: (_name, action) => action() }, {
    execute: async () => { throw new Error('permanent'); },
  }), /permanent/);
});
