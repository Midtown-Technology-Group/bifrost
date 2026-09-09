import { ExecutionCoordinator } from "../.build/coordinator.js";
import { reserveDecision } from "../.build/domain.js";
import { capture } from "../.build/errors.js";

export const ORG = "11111111-1111-4111-8111-111111111111";
export const OTHER_ORG = "22222222-2222-4222-8222-222222222222";
export const USER = "33333333-3333-4333-8333-333333333333";
export const OTHER_USER = "44444444-4444-4444-8444-444444444444";
export const NOW = Date.parse("2026-09-09T19:00:00Z");
export const KEY = "test-request-00000001";
export const principal = {
  userId: USER, organizationId: ORG, isPlatformAdmin: false, isProviderOrg: false,
};
export const body = { workflow: "lab.echo.v1", parameters: { message: "synthetic" } };
export const auth = { LAB_ENABLED: "true", LAB_TOKEN: "a".repeat(64), LAB_USER_ID: USER, LAB_ORG_ID: ORG };

// Unit-level ports. These are not simulations/proofs of Cloudflare storage or replay semantics.
export function fixture(now = () => NOW) {
  let record;
  let status = { status: "complete", output: { message: "synthetic" } };
  let ensureError;
  let confirmError;
  let statusError;
  const calls = { ensure: 0, confirm: 0, status: 0, reserve: 0 };
  const store = {
    async reserve(spec) {
      calls.reserve++;
      // Synchronous check-and-write, then snapshot isolation at the port boundary.
      const reservation = reserveDecision(record, structuredClone(spec));
      record = structuredClone(reservation.record);
      return structuredClone(reservation);
    },
    async confirmDispatch() {
      calls.confirm++;
      if (confirmError) throw confirmError;
      record = { ...record, dispatched: true };
    },
    async read() { return structuredClone(record); },
  };
  const runtime = {
    async ensure() { calls.ensure++; if (ensureError) throw ensureError; },
    async status() { calls.status++; if (statusError) throw statusError; return structuredClone(status); },
  };
  return {
    store, runtime, calls,
    coordinator: new ExecutionCoordinator(store, runtime, now),
    get record() { return structuredClone(record); },
    setStatus(value) { status = value; },
    setEnsureError(value) { ensureError = value; },
    setConfirmError(value) { confirmError = value; },
    setStatusError(value) { statusError = value; },
  };
}

export function gatewayFixture() {
  const executions = new Map();
  let submissions = 0;
  let inspections = 0;
  const gateway = {
    async submit(spec) {
      submissions++;
      if (!executions.has(spec.id)) executions.set(spec.id, fixture(Date.now));
      return capture(() => executions.get(spec.id).coordinator.submit(spec));
    },
    async inspect(id, caller) {
      inspections++;
      if (!executions.has(id)) executions.set(id, fixture(Date.now));
      return capture(() => executions.get(id).coordinator.inspect(caller));
    },
  };
  return { gateway, executions, counts: () => ({ submissions, inspections }) };
}

export function request(payload = body, overrides = {}) {
  return new Request("https://lab.invalid/__lab/executions", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Authorization": `Bearer ${auth.LAB_TOKEN}`, "Idempotency-Key": KEY },
    body: JSON.stringify(payload), ...overrides,
  });
}
