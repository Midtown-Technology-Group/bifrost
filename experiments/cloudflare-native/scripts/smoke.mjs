import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { setTimeout as sleep } from "node:timers/promises";

// Exercise a REAL local Wrangler runtime, not the unit-test ports. No remote target option.
const origin = "http://127.0.0.1:8787";
const config = await readFile(new URL("../.dev.vars", import.meta.url), "utf8");
const token = /^LAB_TOKEN=([a-f0-9]{64})$/m.exec(config)?.[1];
assert.ok(token, "Run npm run setup:local first; expected an unquoted generated LAB_TOKEN.");
const key = `smoke-${randomUUID()}`;
const message = `synthetic-${randomUUID()}`;
const payload = { workflow: "lab.echo.v1", parameters: { message } };
async function call(path, { method = "GET", data, authenticated = true } = {}) {
  assert.match(path, /^\/(?:healthz|__lab\/executions(?:\/[a-f0-9]{64})?)$/);
  const headers = { "Content-Type": "application/json", "Idempotency-Key": key };
  if (authenticated) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${origin}${path}`, {
    method, headers, redirect: "error", signal: AbortSignal.timeout(5000),
    ...(data === undefined ? {} : { body: JSON.stringify(data) }),
  });
  const value = await response.json();
  return { response, value };
}
const health = await call("/healthz", { authenticated: false });
assert.equal(health.response.status, 200);
assert.equal(health.value.mode, "synthetic-lab");
const unauthorized = await call("/__lab/executions", { method: "POST", data: payload, authenticated: false });
assert.equal(unauthorized.response.status, 401);
const first = await call("/__lab/executions", { method: "POST", data: payload });
assert.equal(first.response.status, 202, "Native Workflow submission must be acknowledged.");
assert.match(first.value.executionId, /^[a-f0-9]{64}$/);
const path = `/__lab/executions/${first.value.executionId}`;
assert.equal(first.value.statusUrl, path);
assert.equal(first.response.headers.get("Location"), path);
const duplicate = await call("/__lab/executions", { method: "POST", data: payload });
assert.equal(duplicate.response.status, 202);
assert.equal(duplicate.value.executionId, first.value.executionId);
assert.equal(duplicate.value.reused, true);
const conflict = await call("/__lab/executions", {
  method: "POST", data: { ...payload, parameters: { message: "changed" } },
});
assert.equal(conflict.response.status, 409);
assert.equal(conflict.value.error, "idempotency_conflict");
// Bounded waiting for asynchronous completion, not retries of a failing test.
const deadline = Date.now() + 30_000;
let complete = false;
while (Date.now() < deadline) {
  const status = await call(path);
  assert.equal(status.response.status, 200, "Native status lookup must succeed.");
  assert.equal(status.value.dispatch, "confirmed");
  if (status.value.runtimeStatus === "complete") {
    assert.deepEqual(status.value.output, { message });
    complete = true;
    break;
  }
  assert.ok(["queued", "running", "waiting"].includes(status.value.runtimeStatus), "Unexpected terminal or paused native state.");
  await sleep(100);
}
assert.ok(complete, "Local Workflow did not complete within the smoke-test deadline.");
console.log("PASS: real local HTTP/auth -> Durable Object admission -> Workflow -> status, replay and conflict.");
