import assert from "node:assert/strict";
import test from "node:test";
import { handleRequest } from "../.build/http.js";
import { auth, body, KEY, OTHER_ORG, OTHER_USER, gatewayFixture, request } from "./helpers.mjs";

const headers = () => ({ "Content-Type": "application/json", Authorization: `Bearer ${auth.LAB_TOKEN}`, "Idempotency-Key": KEY });
const get = (path, method = "GET") => new Request(`https://lab.invalid${path}`, { method, headers: headers() });
async function error(response, status, code) {
  assert.equal(response.status, status);
  assert.deepEqual(await response.json(), { error: code });
  assert.equal(response.headers.get("Cache-Control"), "no-store");
}

test("lab is disabled by default, including its health endpoint", async () => {
  const f = gatewayFixture();
  await error(await handleRequest(request(), {}, f.gateway), 404, "not_found");
  await error(await handleRequest(get("/healthz"), { ...auth, LAB_ENABLED: "false" }, f.gateway), 404, "not_found");
  assert.deepEqual(f.counts(), { submissions: 0, inspections: 0 });
});

test("enabled health check reveals only the synthetic lab mode", async () => {
  const response = await handleRequest(new Request("https://lab.invalid/healthz"), { LAB_ENABLED: "true" }, gatewayFixture().gateway);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { mode: "synthetic-lab" });
});

for (const malformed of [{ LAB_TOKEN: undefined }, { LAB_TOKEN: "short" }, { LAB_USER_ID: "invalid" }, { LAB_ORG_ID: undefined }]) {
  test(`incomplete lab identity configuration fails closed: ${JSON.stringify(malformed)}`, async () => {
    const f = gatewayFixture();
    const response = await handleRequest(request(), { ...auth, ...malformed }, f.gateway);
    assert.equal(response.headers.get("Retry-After"), "5");
    await error(response, 503, "lab_not_configured");
    assert.equal(f.counts().submissions, 0);
  });
}

for (const value of [undefined, "Bearer wrong", "a".repeat(200)]) {
  test(`unauthorized request never reaches admission (${value?.length ?? 0} header characters)`, async () => {
    const f = gatewayFixture();
    const h = headers();
    if (value === undefined) delete h.Authorization; else h.Authorization = value;
    const response = await handleRequest(request(body, { headers: h }), auth, f.gateway);
    assert.equal(response.headers.get("WWW-Authenticate"), "Bearer");
    await error(response, 401, "unauthorized");
    assert.deepEqual(f.counts(), { submissions: 0, inspections: 0 });
  });
}

test("HTTP submit, same-key replay and authorized status form one happy path", async () => {
  const f = gatewayFixture();
  const response = await handleRequest(request(), auth, f.gateway);
  assert.equal(response.status, 202);
  const accepted = await response.json();
  assert.match(accepted.executionId, /^[a-f0-9]{64}$/);
  assert.equal(accepted.reused, false);
  assert.equal(response.headers.get("Location"), accepted.statusUrl);
  const repeated = await handleRequest(request(), auth, f.gateway);
  assert.equal(repeated.status, 202);
  assert.deepEqual(await repeated.json(), { ...accepted, reused: true });
  const status = await handleRequest(get(accepted.statusUrl), auth, f.gateway);
  assert.equal(status.status, 200);
  assert.deepEqual((await status.json()).output, { message: "synthetic" });
  assert.equal(f.executions.get(accepted.executionId).calls.ensure, 1);
});

test("changed input produces a 409 through a serialized RPC outcome", async () => {
  const f = gatewayFixture();
  const gateway = { ...f.gateway, submit: async (spec) => JSON.parse(JSON.stringify(await f.gateway.submit(spec))) };
  await handleRequest(request(), auth, gateway);
  await error(await handleRequest(request({ ...body, parameters: { message: "changed" } }), auth, gateway), 409, "idempotency_conflict");
});

for (const payload of [
  { ...body, scope: null },
  { ...body, scope: OTHER_ORG },
]) {
  test(`client cannot select unauthorized scope: ${JSON.stringify(payload.scope)}`, async () => {
    const f = gatewayFixture();
    await error(await handleRequest(request(payload), auth, f.gateway), 403, "scope_not_allowed");
    assert.equal(f.counts().submissions, 0);
  });
}

test("client-supplied privilege flags are rejected instead of trusted", async () => {
  await error(await handleRequest(request({ ...body, isPlatformAdmin: true }), auth, gatewayFixture().gateway), 400, "invalid_request");
});

test("unregistered workflows are not executable", async () => {
  await error(await handleRequest(request({ ...body, workflow: "customer.script" }), auth, gatewayFixture().gateway), 400, "unsupported_workflow");
});

test("every submission requires a bounded idempotency key", async () => {
  const h = headers(); delete h["Idempotency-Key"];
  await error(await handleRequest(request(body, { headers: h }), auth, gatewayFixture().gateway), 400, "invalid_idempotency_key");
});

test("malformed JSON is a client error", async () => {
  await error(await handleRequest(request(body, { body: "{" }), auth, gatewayFixture().gateway), 400, "invalid_json");
});

test("malformed UTF-8 is a client error", async () => {
  await error(await handleRequest(request(body, { body: new Uint8Array([255]) }), auth, gatewayFixture().gateway), 400, "invalid_json");
});

test("JSON content type is required", async () => {
  await error(await handleRequest(request(body, { headers: { ...headers(), "Content-Type": "text/plain" } }), auth, gatewayFixture().gateway), 415, "json_required");
});

test("encoded request bodies are not accepted", async () => {
  await error(await handleRequest(request(body, { headers: { ...headers(), "Content-Encoding": "gzip" } }), auth, gatewayFixture().gateway), 415, "encoded_body_not_supported");
});

test("declared oversized bodies are rejected before admission", async () => {
  await error(await handleRequest(request(body, { headers: { ...headers(), "Content-Length": "10000" } }), auth, gatewayFixture().gateway), 413, "request_too_large");
});

test("body limit is enforced on streamed bytes without Content-Length", async () => {
  let cancelled = false;
  const stream = new ReadableStream({
    pull(controller) { controller.enqueue(new Uint8Array(2100)); },
    cancel() { cancelled = true; },
  });
  await error(await handleRequest(request(body, { body: stream, duplex: "half" }), auth, gatewayFixture().gateway), 413, "request_too_large");
  assert.equal(cancelled, true);
});

test("unexpected gateway failures do not leak internal details", async () => {
  const f = gatewayFixture();
  const gateway = { ...f.gateway, submit: async () => { throw new Error("secret=private"); } };
  await error(await handleRequest(request(), auth, gateway), 500, "internal_error");
});

test("same-tenant different requester cannot inspect a run", async () => {
  const f = gatewayFixture();
  const accepted = await (await handleRequest(request(), auth, f.gateway)).json();
  await error(await handleRequest(get(accepted.statusUrl), { ...auth, LAB_USER_ID: OTHER_USER }, f.gateway), 404, "execution_not_found");
  assert.equal(f.executions.get(accepted.executionId).calls.status, 0);
});

test("query parameters cannot smuggle an alternate principal or scope", async () => {
  const f = gatewayFixture();
  await error(await handleRequest(get(`/__lab/executions?scope=${OTHER_ORG}`), auth, f.gateway), 400, "query_parameters_not_supported");
});

test("wrong HTTP methods advertise the one allowed method", async () => {
  const f = gatewayFixture();
  const response = await handleRequest(get("/__lab/executions"), auth, f.gateway);
  assert.equal(response.headers.get("Allow"), "POST");
  await error(response, 405, "method_not_allowed");
});

test("unknown status IDs remain read-only and are not found", async () => {
  const f = gatewayFixture();
  await error(await handleRequest(get(`/__lab/executions/${"f".repeat(64)}`), auth, f.gateway), 404, "execution_not_found");
  assert.equal(f.counts().submissions, 0);
});

test("an ambiguous provider submission preserves same-key retry wording over HTTP", async () => {
  const f = gatewayFixture();
  const gateway = { ...f.gateway, submit: async () => ({ ok: false, fault: { status: 503, code: "dispatch_unconfirmed_retry_same_key" } }) };
  const response = await handleRequest(request(), auth, gateway);
  assert.equal(response.headers.get("Retry-After"), "5");
  await error(response, 503, "dispatch_unconfirmed_retry_same_key");
});
