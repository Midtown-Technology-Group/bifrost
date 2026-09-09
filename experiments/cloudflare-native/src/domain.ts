import { Fault } from "./errors.js";
import { resolveScope } from "./scope.js";
import type { Principal } from "./scope.js";

export const WORKFLOW = "lab.echo.v1" as const;
export const BODY_LIMIT = 4096;
export const DISPATCH_WINDOW_MS = 15 * 60 * 1000;
export const EXECUTION_ID = /^[a-f0-9]{64}$/;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;

export interface EchoInput {
  readonly workflow: typeof WORKFLOW;
  readonly message: string;
}
export interface Submission {
  readonly input: EchoInput;
  readonly scope: string | null | undefined;
}
export interface ExecutionSpec {
  readonly id: string;
  readonly userId: string;
  readonly callerOrganizationId: string | null;
  readonly organizationId: string | null;
  readonly input: EchoInput;
  readonly fingerprint: string;
  readonly createdAt: string;
}
export interface ExecutionRecord {
  readonly spec: ExecutionSpec;
  readonly dispatched: boolean;
}
export interface Reservation {
  readonly record: ExecutionRecord;
  readonly reused: boolean;
}
export interface Accepted {
  readonly executionId: string;
  readonly reused: boolean;
}

export function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function uuid(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value)) throw new Fault(400, "invalid_uuid");
  return value.toLowerCase();
}

export function idempotencyKey(value: string | null): string {
  if (value === null || !/^[A-Za-z0-9._:-]{16,128}$/.test(value)) {
    throw new Fault(400, "invalid_idempotency_key");
  }
  return value;
}

export function parseSubmission(value: unknown): Submission {
  if (!object(value) || Object.keys(value).some((key) => !["workflow", "parameters", "scope"].includes(key))) {
    throw new Fault(400, "invalid_request");
  }
  if (value.workflow !== WORKFLOW) throw new Fault(400, "unsupported_workflow");
  if (!object(value.parameters) || Object.keys(value.parameters).some((key) => key !== "message")) {
    throw new Fault(400, "invalid_parameters");
  }
  const message = value.parameters.message;
  if (typeof message !== "string" || message.length < 1 || message.length > 1024) {
    throw new Fault(400, "invalid_message");
  }
  const scope = value.scope === undefined || value.scope === null ? value.scope : uuid(value.scope);
  return { input: { workflow: WORKFLOW, message }, scope };
}

export async function sha256(text: string): Promise<string> {
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function prepareSpec(
  caller: Principal,
  submission: Submission,
  key: string,
  now: number = Date.now(),
): Promise<ExecutionSpec> {
  const organizationId = resolveScope(caller, submission.scope);
  // Include requester AND caller/effective orgs: a reused key must not reveal another user's run.
  const id = await sha256(JSON.stringify([
    "bifrost.cf-lab.execution.v1", caller.userId, caller.organizationId, organizationId, idempotencyKey(key),
  ]));
  const fingerprint = await sha256(JSON.stringify(submission.input));
  return {
    id, userId: caller.userId, callerOrganizationId: caller.organizationId, organizationId,
    input: submission.input, fingerprint, createdAt: new Date(now).toISOString(),
  };
}

/** Execute this decision inside an atomic storage transaction. It performs no I/O. */
export function reserveDecision(existing: ExecutionRecord | undefined, spec: ExecutionSpec): Reservation {
  if (existing === undefined) return { record: { spec, dispatched: false }, reused: false };
  const previous = existing.spec;
  if (previous.id !== spec.id || previous.userId !== spec.userId ||
      previous.callerOrganizationId !== spec.callerOrganizationId || previous.organizationId !== spec.organizationId ||
      previous.fingerprint !== spec.fingerprint || JSON.stringify(previous.input) !== JSON.stringify(spec.input)) {
    throw new Fault(409, "idempotency_conflict");
  }
  return { record: existing, reused: true };
}

export function assertVisible(spec: ExecutionSpec, caller: Principal): void {
  if (spec.userId !== caller.userId || spec.callerOrganizationId !== caller.organizationId) {
    throw new Fault(404, "execution_not_found");
  }
  // Re-evaluate any cross-org grant rather than retaining a revoked privilege.
  if (spec.organizationId !== caller.organizationId) {
    try { resolveScope(caller, spec.organizationId); }
    catch { throw new Fault(404, "execution_not_found"); }
  }
}
