import {
  EXECUTION_ID_PATTERN, InputError, MAX_BODY_BYTES, WORKFLOW_ID,
  isObject, isPrincipal, makeCommand, publicStatus, sha256,
  type ExecutionCommand, type Principal,
} from "./contracts.ts";
import type { InspectResult, SubmitResult } from "./admission.ts";

export interface LabConfig { LAB_ENABLED?: string; LAB_PRINCIPALS?: string }
export interface ApiPort {
  submit(command: ExecutionCommand): Promise<SubmitResult>;
  inspect(owner: Principal, executionId: string): Promise<InspectResult>;
}
function json(value: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return Response.json(value, { status, headers: {
    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", ...headers,
  } });
}
async function authenticate(request: Request, config: LabConfig): Promise<Principal | null> {
  // Local fixture auth only. No trust in X-Org-Id, org_id, run_as, or Access headers.
  const principals: unknown = JSON.parse(config.LAB_PRINCIPALS ?? "null");
  if (!isObject(principals) || Object.keys(principals).length < 1 ||
      Object.keys(principals).length > 8 || !Object.entries(principals).every(
        ([token, principal]) => token.length >= 32 && token.length <= 256 && isPrincipal(principal),
      )) throw new Error("Lab principal configuration unavailable");
  const authorization = request.headers.get("Authorization");
  if (!authorization?.startsWith("Bearer ") || authorization.length > 263) return null;
  const candidate = await sha256(authorization.slice(7));
  // Compare digests, not secret token prefixes. This is NOT production identity verification.
  let owner: Principal | null = null;
  for (const [token, principal] of Object.entries(principals)) {
    const expected = await sha256(token);
    let different = 0;
    for (let i = 0; i < expected.length; i++) different |= expected.charCodeAt(i) ^ candidate.charCodeAt(i);
    if (different === 0) owner = principal as Principal;
  }
  return owner;
}
class BodyTooLarge extends Error {}
async function readJson(request: Request): Promise<unknown> {
  if (Number(request.headers.get("Content-Length") ?? 0) > MAX_BODY_BYTES) throw new BodyTooLarge();
  const reader = request.body?.getReader();
  if (!reader) throw new InputError("A JSON body is required");
  let size = 0;
  const chunks: Uint8Array[] = [];
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BODY_BYTES) {
        await reader.cancel();
        throw new BodyTooLarge();
      }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.length; }
  try { return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body)); }
  catch { throw new InputError("Invalid UTF-8 JSON body"); }
}
export async function handleRequest(request: Request, config: LabConfig, api: ApiPort): Promise<Response> {
  try {
    if (config.LAB_ENABLED !== "true") return json({ error: "Lab is disabled" }, 503);
    const owner = await authenticate(request, config);
    if (!owner) return json({ error: "Unauthorized" }, 401, { "WWW-Authenticate": "Bearer" });
    const path = new URL(request.url).pathname;
    if (request.method === "POST" && path === "/lab/executions") {
      if (request.headers.get("Content-Type")?.split(";")[0]?.trim().toLowerCase() !== "application/json") {
        return json({ error: "Content-Type must be application/json" }, 415);
      }
      const command = await makeCommand(owner, request.headers.get("Idempotency-Key"), await readJson(request));
      const result = await api.submit(command);
      if (result.kind === "conflict") return json({ error: "Idempotency key already has a different request" }, 409);
      if (result.kind === "expired") return json({ error: "Admission retry window expired; inspect the original execution before resubmitting",
        execution_id: command.execution_id, status_url: `/lab/executions/${command.execution_id}` }, 409);
      const location = `/lab/executions/${command.execution_id}`;
      return json({ execution_id: command.execution_id, workflow_id: WORKFLOW_ID,
        replayed: result.replayed, status_url: location }, 202, { Location: location });
    }
    const match = /^\/lab\/executions\/(lab_[a-f0-9]{64})$/.exec(path);
    if (request.method === "GET" && match && EXECUTION_ID_PATTERN.test(match[1]!)) {
      const result = await api.inspect(owner, match[1]!);
      if (result.kind === "not_found") return json({ error: "Execution not found" }, 404);
      return json({ execution_id: match[1], workflow_id: WORKFLOW_ID,
        status: publicStatus(result.runtime.status), runtime_status: result.runtime.status,
        result: result.runtime.status === "complete" ? result.runtime.output ?? null : null });
    }
    return json({ error: "Route not found" }, 404);
  } catch (error) {
    if (error instanceof BodyTooLarge) return json({ error: "Request body exceeds 4096 bytes" }, 413);
    if (error instanceof InputError) return json({ error: error.message }, 422);
    // Never reflect binding/provider errors, tokens, or execution payloads to clients.
    return json({ error: "Lab temporarily unavailable; retry POST with the same Idempotency-Key" }, 503,
      { "Retry-After": "5" });
  }
}
