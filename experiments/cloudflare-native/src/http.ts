import { Fault, unwrap } from "./errors.js";
import type { Outcome } from "./errors.js";
import { BODY_LIMIT, EXECUTION_ID, idempotencyKey, parseSubmission, prepareSpec, sha256, uuid } from "./domain.js";
import type { Accepted, ExecutionSpec } from "./domain.js";
import type { ExecutionView } from "./coordinator.js";
import type { Principal } from "./scope.js";

export interface LabAuth {
  LAB_ENABLED?: string;
  LAB_TOKEN?: string;
  LAB_USER_ID?: string;
  LAB_ORG_ID?: string;
}
export interface Gateway {
  submit(spec: ExecutionSpec): Promise<Outcome<Accepted>>;
  inspect(id: string, caller: Principal): Promise<Outcome<ExecutionView>>;
}

function json(value: unknown, status = 200, extra: Record<string, string> = {}): Response {
  return Response.json(value, { status, headers: {
    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", ...extra,
  } });
}

async function authenticate(request: Request, env: LabAuth): Promise<Principal> {
  if (!env.LAB_TOKEN || !/^[a-f0-9]{64}$/.test(env.LAB_TOKEN)) throw new Fault(503, "lab_not_configured");
  let userId: string;
  let organizationId: string;
  try { userId = uuid(env.LAB_USER_ID); organizationId = uuid(env.LAB_ORG_ID); }
  catch { throw new Fault(503, "lab_not_configured"); }
  const supplied = request.headers.get("Authorization") ?? "";
  if (supplied.length > 128) throw new Fault(401, "unauthorized");
  // Compare fixed-length digests; neither tokens nor auth headers enter durable state.
  const [actual, expected] = await Promise.all([sha256(supplied), sha256(`Bearer ${env.LAB_TOKEN}`)]);
  let difference = 0;
  for (let i = 0; i < expected.length; i++) difference |= actual.charCodeAt(i) ^ expected.charCodeAt(i);
  if (difference !== 0) throw new Fault(401, "unauthorized");
  return { userId, organizationId, isPlatformAdmin: false, isProviderOrg: false };
}

async function readJson(request: Request): Promise<unknown> {
  if (request.headers.get("Content-Type")?.split(";")[0]?.trim().toLowerCase() !== "application/json") {
    throw new Fault(415, "json_required");
  }
  if (request.headers.has("Content-Encoding")) throw new Fault(415, "encoded_body_not_supported");
  const length = request.headers.get("Content-Length");
  if (length !== null && Number(length) > BODY_LIMIT) throw new Fault(413, "request_too_large");
  if (request.body === null) throw new Fault(400, "invalid_json");
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > BODY_LIMIT) {
        await reader.cancel();
        throw new Fault(413, "request_too_large");
      }
      chunks.push(chunk.value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  try { return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); }
  catch { throw new Fault(400, "invalid_json"); }
}

export async function handleRequest(request: Request, env: LabAuth, gateway: Gateway): Promise<Response> {
  if (env.LAB_ENABLED !== "true") return json({ error: "not_found" }, 404);
  try {
    const url = new URL(request.url);
    if (url.pathname === "/healthz" && request.method === "GET") return json({ mode: "synthetic-lab" });
    const caller = await authenticate(request, env);
    if (url.search !== "") throw new Fault(400, "query_parameters_not_supported");
    if (url.pathname === "/__lab/executions") {
      if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405, { Allow: "POST" });
      const key = idempotencyKey(request.headers.get("Idempotency-Key"));
      const submission = parseSubmission(await readJson(request));
      const spec = await prepareSpec(caller, submission, key);
      const accepted = unwrap(await gateway.submit(spec));
      const location = `/__lab/executions/${accepted.executionId}`;
      return json({ ...accepted, statusUrl: location }, 202, { Location: location });
    }
    const match = /^\/__lab\/executions\/([a-f0-9]{64})$/.exec(url.pathname);
    if (match?.[1] && EXECUTION_ID.test(match[1])) {
      if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405, { Allow: "GET" });
      return json(unwrap(await gateway.inspect(match[1], caller)));
    }
    return json({ error: "not_found" }, 404);
  } catch (error) {
    const fault = error instanceof Fault ? error : new Fault(500, "internal_error");
    const headers: Record<string, string> = {};
    if (fault.status === 401) headers["WWW-Authenticate"] = "Bearer";
    if (fault.status === 503) headers["Retry-After"] = "5";
    return json({ error: fault.code }, fault.status, headers);
  }
}
