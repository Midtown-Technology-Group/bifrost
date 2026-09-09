import test from "node:test";
import assert from "node:assert/strict";
import { parseSubmission, prepareSpec, reserveDecision, idempotencyKey, uuid, assertVisible } from "../.build/domain.js";
import { body, principal, KEY, NOW, ORG, OTHER_ORG, OTHER_USER } from "./helpers.mjs";

const spec = (payload = body, caller = principal, key = KEY, now = NOW) =>
  prepareSpec(caller, parseSubmission(payload), key, now);

test("scope omission and explicit null stay distinct", () => {
  assert.equal(parseSubmission(body).scope, undefined);
  assert.equal(parseSubmission({ ...body, scope: null }).scope, null);
});
test("canonical UUID parsing normalizes case without accepting arbitrary identifiers", () => {
  assert.equal(uuid("AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"), "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  assert.throws(() => uuid("global"), { code: "invalid_uuid" });
});
for (const value of [null, [], 5, { ...body, organizationId: OTHER_ORG }, { ...body, isPlatformAdmin: true },
  { ...body, workflow: "run-shell" }, { ...body, parameters: {} }, { ...body, parameters: { message: "x", token: "secret" } },
  { ...body, parameters: { message: "" } }, { ...body, parameters: { message: "x".repeat(1025) } },
  { ...body, scope: 42 }, { ...body, scope: "not-an-org" }]) {
  test(`reject invalid input ${JSON.stringify(value).slice(0, 100)}`, () => assert.throws(() => parseSubmission(value)));
}
test("idempotency keys are required and bounded", () => {
  for (const value of [null, "", "short", "x".repeat(129), "white space-00000000"]) assert.throws(() => idempotencyKey(value));
  assert.equal(idempotencyKey(KEY), KEY);
});
test("request field order and implicit/explicit own scope produce the same identity", async () => {
  const a = await spec();
  const b = await spec({ scope: ORG, parameters: { message: "synthetic" }, workflow: body.workflow });
  assert.equal(a.id, b.id);
  assert.equal(a.fingerprint, b.fingerprint);
});
test("requester, caller org, effective org and key each isolate execution IDs", async () => {
  const cases = [
    await spec(),
    await spec(body, { ...principal, userId: OTHER_USER }),
    await spec(body, { ...principal, organizationId: OTHER_ORG }),
    await spec({ ...body, scope: OTHER_ORG }, { ...principal, isProviderOrg: true }),
    await spec(body, principal, "another-key-00000001"),
    await spec({ ...body, scope: null }, { ...principal, isPlatformAdmin: true }),
  ];
  assert.equal(new Set(cases.map((value) => value.id)).size, cases.length);
  for (const value of cases) assert.match(value.id, /^[a-f0-9]{64}$/);
});
test("a new submission reserves immutable input, not credentials", async () => {
  const prepared = await spec();
  const result = reserveDecision(undefined, prepared);
  assert.equal(result.reused, false);
  assert.equal(result.record.dispatched, false);
  assert.equal("token" in prepared, false);
  assert.equal("isPlatformAdmin" in prepared, false);
});
test("same key with changed input conflicts instead of accepting or dispatching", async () => {
  const a = await spec();
  const b = await spec({ ...body, parameters: { message: "different" } });
  assert.equal(a.id, b.id);
  assert.notEqual(a.fingerprint, b.fingerprint);
  assert.throws(() => reserveDecision({ spec: a, dispatched: false }, b), { status: 409 });
});
test("replays retain original creation time and dispatch marker", async () => {
  const first = await spec();
  const later = await spec(body, principal, KEY, NOW + 10000);
  const existing = { spec: first, dispatched: true };
  const replay = reserveDecision(existing, later);
  assert.equal(replay.reused, true);
  assert.deepEqual(replay.record, existing);
});
test("execution visibility is requester-specific, not just tenant-specific", async () => {
  const value = await spec();
  assertVisible(value, principal);
  assert.throws(() => assertVisible(value, { ...principal, userId: OTHER_USER }), { status: 404 });
  assert.throws(() => assertVisible(value, { ...principal, organizationId: OTHER_ORG }), { status: 404 });
});
test("revoked cross-org privileges cannot read a previously scoped run", async () => {
  const value = await spec({ ...body, scope: OTHER_ORG }, { ...principal, isProviderOrg: true });
  assert.throws(() => assertVisible(value, principal), { status: 404 });
  assertVisible(value, { ...principal, isProviderOrg: true });
});
