/** Experimental lab contracts, NOT replacements for the Python API/SDK DTOs. */
export const WORKFLOW_ID = "lab.inventory.v1" as const;
export const MAX_TARGETS = 8;
export const MAX_BODY_BYTES = 4096;
export const ADMISSION_RETRY_WINDOW_MS = 10 * 60 * 1000;
export const EXECUTION_ID_PATTERN = /^lab_[a-f0-9]{64}$/;

export type Principal = { org_id: string; subject: string };
export type ExecutionRequest = {
  workflow_id: typeof WORKFLOW_ID;
  input_data: { targets: string[] };
};
export type ExecutionCommand = {
  version: 1;
  execution_id: string;
  owner: Principal;
  request: ExecutionRequest;
  fingerprint: string;
};

export class InputError extends Error {}
export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
export function isPrincipal(value: unknown): value is Principal {
  return isObject(value) && [value.org_id, value.subject].every(
    (part) => typeof part === "string" && /^[a-zA-Z0-9_-]{1,80}$/.test(part),
  );
}
export function samePrincipal(left: Principal, right: Principal): boolean {
  return left.org_id === right.org_id && left.subject === right.subject;
}
function onlyKeys(value: Record<string, unknown>, allowed: string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key));
}
export function parseExecutionRequest(value: unknown): ExecutionRequest {
  if (!isObject(value) || !onlyKeys(value, ["workflow_id", "input_data"]) ||
      value.workflow_id !== WORKFLOW_ID || !isObject(value.input_data) ||
      !onlyKeys(value.input_data, ["targets"])) {
    throw new InputError("Only lab.inventory.v1 with input_data.targets is supported");
  }
  const targets = value.input_data.targets;
  if (!Array.isArray(targets) || targets.length < 1 || targets.length > MAX_TARGETS ||
      !targets.every((target) => typeof target === "string" && /^demo-[a-z0-9-]{1,32}$/.test(target)) ||
      new Set(targets).size !== targets.length) {
    throw new InputError(`Supply 1-${MAX_TARGETS} distinct demo-* target identifiers`);
  }
  // Reconstruct a canonical, small DTO: key order from the caller is irrelevant.
  return { workflow_id: WORKFLOW_ID, input_data: { targets: [...targets] } };
}
export async function sha256(value: string): Promise<string> {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(bytes)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
export async function makeCommand(
  owner: Principal, key: string | null, input: unknown,
): Promise<ExecutionCommand> {
  if (!isPrincipal(owner)) throw new InputError("Invalid principal");
  if (!key || !/^[A-Za-z0-9._:-]{1,128}$/.test(key)) {
    throw new InputError("Idempotency-Key must be 1-128 ASCII letters, numbers, or ._:-");
  }
  const request = parseExecutionRequest(input);
  // Delimited JSON avoids concatenation collisions. Identity is NOT client-supplied.
  const id = await sha256(JSON.stringify(["lab-v1", owner.org_id, owner.subject, key]));
  return {
    version: 1, execution_id: `lab_${id}`, owner: { ...owner }, request,
    fingerprint: await sha256(JSON.stringify(request)),
  };
}
export type RuntimeStatus = { status: string; output?: unknown };
export function publicStatus(runtime: string): string {
  const names: Record<string, string> = {
    queued: "Pending", running: "Running", waiting: "Running",
    waitingForPause: "Running", paused: "Paused", complete: "Success",
    errored: "Failed", terminated: "Cancelled",
  };
  return Object.hasOwn(names, runtime) ? names[runtime]! : "Unknown";
}
