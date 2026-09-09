import assert from "node:assert/strict";
import test from "node:test";
import { prepareSpec, parseSubmission, DISPATCH_WINDOW_MS } from "../.build/domain.js";
import { capture, unwrap } from "../.build/errors.js";
import { fixture, principal, body, KEY, NOW, OTHER_USER, OTHER_ORG } from "./helpers.mjs";

const spec = () => prepareSpec(principal, parseSubmission(body), KEY, NOW);
const rejects = (promise, status, code) => assert.rejects(promise, { status, code });

test("acceptance follows native acknowledgement and a durable dispatch marker", async () => {
  const f = fixture();
  const input = await spec();
  assert.deepEqual(await f.coordinator.submit(input), { executionId: input.id, reused: false });
  assert.equal(f.record.dispatched, true);
  assert.deepEqual(f.calls, { reserve: 1, ensure: 1, confirm: 1, status: 0 });
});

test("acknowledged replay never recreates a Workflow, even after native history expires", async () => {
  let now = NOW;
  const f = fixture(() => now);
  const input = await spec();
  await f.coordinator.submit(input);
  now += 90 * 24 * 60 * 60 * 1000;
  f.setEnsureError(new Error("must not call create"));
  f.setStatusError(new Error("history unavailable"));
  assert.deepEqual(await f.coordinator.submit({ ...input, createdAt: new Date(now).toISOString() }), {
    executionId: input.id, reused: true,
  });
  assert.equal(f.calls.ensure, 1);
  await rejects(f.coordinator.inspect(principal), 503, "runtime_status_unavailable");
});

test("a changed payload conflicts without launching a second Workflow", async () => {
  const f = fixture();
  await f.coordinator.submit(await spec());
  const changed = await prepareSpec(principal, parseSubmission({ ...body, parameters: { message: "changed" } }), KEY, NOW);
  await rejects(f.coordinator.submit(changed), 409, "idempotency_conflict");
  assert.equal(f.calls.ensure, 1);
});

test("unconfirmed dispatch returns a same-key retry instruction, not acceptance", async () => {
  const f = fixture();
  f.setEnsureError(new Error("provider timeout with sensitive details"));
  await rejects(f.coordinator.submit(await spec()), 503, "dispatch_unconfirmed_retry_same_key");
  assert.equal(f.record.dispatched, false);
  assert.equal(f.calls.confirm, 0);
});

test("same-key retry recovers an unacknowledged dispatch using the original ID", async () => {
  const f = fixture();
  const input = await spec();
  f.setEnsureError(new Error("acknowledgement lost"));
  await rejects(f.coordinator.submit(input), 503, "dispatch_unconfirmed_retry_same_key");
  f.setEnsureError(undefined);
  assert.deepEqual(await f.coordinator.submit(input), { executionId: input.id, reused: true });
  assert.equal(f.record.dispatched, true);
  assert.equal(f.calls.ensure, 2); // Port retries; provider-side deduplication requires runtime validation.
});

test("failure to persist acknowledgement does not falsely report acceptance", async () => {
  const f = fixture();
  const input = await spec();
  f.setConfirmError(new Error("storage unavailable"));
  await rejects(f.coordinator.submit(input), 503, "dispatch_unconfirmed_retry_same_key");
  assert.equal(f.record.dispatched, false);
  f.setConfirmError(undefined);
  assert.equal((await f.coordinator.submit(input)).reused, true);
  assert.equal(f.record.dispatched, true);
});

test("an ambiguous submission at the recovery deadline is not dispatched again", async () => {
  let now = NOW;
  const f = fixture(() => now);
  const input = await spec();
  await f.store.reserve(input);
  now += DISPATCH_WINDOW_MS;
  await rejects(f.coordinator.submit(input), 409, "submission_recovery_expired");
  assert.equal(f.calls.ensure, 0);
});

test("retries cannot refresh the original recovery deadline", async () => {
  let now = NOW;
  const f = fixture(() => now);
  const input = await spec();
  await f.store.reserve(input);
  now += DISPATCH_WINDOW_MS + 1;
  await rejects(f.coordinator.submit({ ...input, createdAt: new Date(now).toISOString() }), 409, "submission_recovery_expired");
  assert.equal(f.record.spec.createdAt, input.createdAt);
  assert.equal(f.calls.ensure, 0);
});

test("reading unconfirmed admission is read-only and does not infer execution state", async () => {
  const f = fixture();
  await f.store.reserve(await spec());
  const result = await f.coordinator.inspect(principal);
  assert.equal(result.dispatch, "unconfirmed");
  assert.equal(result.runtimeStatus, null);
  assert.equal(result.output, null);
  assert.equal(f.calls.ensure, 0);
  assert.equal(f.calls.status, 0);
});

for (const stranger of [
  { ...principal, userId: OTHER_USER },
  { ...principal, organizationId: OTHER_ORG },
  { ...principal, userId: OTHER_USER, isPlatformAdmin: true },
]) {
  test(`requester visibility precedes runtime lookup: ${JSON.stringify(stranger)}`, async () => {
    const f = fixture();
    await f.coordinator.submit(await spec());
    await rejects(f.coordinator.inspect(stranger), 404, "execution_not_found");
    assert.equal(f.calls.status, 0);
  });
}

test("a missing admission record is not found", async () => {
  await rejects(fixture().coordinator.inspect(principal), 404, "execution_not_found");
});

for (const state of ["queued", "running", "paused", "terminated", "waiting", "waitingForPause", "unknown"]) {
  test(`native state is exposed without inventing lifecycle transitions: ${state}`, async () => {
    const f = fixture();
    await f.coordinator.submit(await spec());
    f.setStatus({ status: state });
    const result = await f.coordinator.inspect(principal);
    assert.equal(result.runtimeStatus, state);
    assert.equal(result.output, null);
    assert.equal(result.error, null);
  });
}

test("native failure details do not cross the public boundary", async () => {
  const f = fixture();
  await f.coordinator.submit(await spec());
  f.setStatus({ status: "errored", error: { message: "provider secret" }, output: "private details" });
  const result = await f.coordinator.inspect(principal);
  assert.deepEqual(result.error, { code: "workflow_failed" });
  assert.equal(JSON.stringify(result).includes("secret"), false);
});

test("successful output is allowlisted rather than copied wholesale", async () => {
  const f = fixture();
  await f.coordinator.submit(await spec());
  f.setStatus({ status: "complete", output: { message: "synthetic", token: "private" } });
  assert.deepEqual((await f.coordinator.inspect(principal)).output, { message: "synthetic" });
});

for (const invalid of [
  { status: "new-unknown-state" },
  { status: "complete" },
  { status: "complete", output: { message: "different" } },
]) {
  test(`runtime contract drift fails explicitly: ${JSON.stringify(invalid)}`, async () => {
    const f = fixture();
    await f.coordinator.submit(await spec());
    f.setStatus(invalid);
    await rejects(f.coordinator.inspect(principal), 502, "runtime_contract_error");
  });
}

test("concurrent callers reserve one immutable record and converge on one identifier", async () => {
  const f = fixture();
  const input = await spec();
  const results = await Promise.all(Array.from({ length: 12 }, () => f.coordinator.submit(input)));
  assert.equal(results.filter((result) => !result.reused).length, 1);
  assert.deepEqual(new Set(results.map((result) => result.executionId)), new Set([input.id]));
  assert.equal(f.record.dispatched, true);
  // This covers coordinator behavior only, NOT distributed Cloudflare execution counts.
});

test("structured fault outcomes survive JSON transport without custom Error prototypes", async () => {
  const f = fixture();
  await f.coordinator.submit(await spec());
  const changed = await prepareSpec(principal, parseSubmission({ ...body, parameters: { message: "other" } }), KEY, NOW);
  const transported = JSON.parse(JSON.stringify(await capture(() => f.coordinator.submit(changed))));
  assert.throws(() => unwrap(transported), { status: 409, code: "idempotency_conflict" });
});

test("unexpected internal exceptions become sanitized fault outcomes", async () => {
  assert.deepEqual(await capture(async () => { throw new Error("credentials=private"); }), {
    ok: false, fault: { status: 500, code: "internal_error" },
  });
});
