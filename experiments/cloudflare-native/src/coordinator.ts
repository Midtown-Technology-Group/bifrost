import { Fault } from "./errors.js";
import { assertVisible, DISPATCH_WINDOW_MS, object } from "./domain.js";
import type { Accepted, ExecutionRecord, ExecutionSpec, Reservation } from "./domain.js";
import type { Principal } from "./scope.js";

export interface AdmissionStore {
  reserve(spec: ExecutionSpec): Promise<Reservation>;
  confirmDispatch(): Promise<void>;
  read(): Promise<ExecutionRecord | undefined>;
}
export interface WorkflowRuntime {
  /** Must be idempotent while the native Workflow instance is retained. */
  ensure(id: string): Promise<void>;
  status(id: string): Promise<{ status: string; output?: unknown }>;
}
export interface ExecutionView {
  executionId: string;
  organizationId: string | null;
  createdAt: string;
  dispatch: "confirmed" | "unconfirmed";
  runtimeStatus: string | null;
  output: { message: string } | null;
  error: { code: string } | null;
}
const STATES = new Set([
  "queued", "running", "paused", "errored", "terminated", "complete", "waiting", "waitingForPause", "unknown",
]);

export class ExecutionCoordinator {
  constructor(
    private readonly store: AdmissionStore,
    private readonly runtime: WorkflowRuntime,
    private readonly now: () => number = Date.now,
  ) {}

  async submit(spec: ExecutionSpec): Promise<Accepted> {
    const reservation = await this.store.reserve(spec);
    const record = reservation.record;
    if (!record.dispatched) {
      // Never resurrect an old ambiguous submission after native ID retention has expired.
      // Defaults retain Workflows for >=3 days; this lab permits dispatch recovery for only 15 min.
      if (this.now() - Date.parse(record.spec.createdAt) >= DISPATCH_WINDOW_MS) {
        throw new Fault(409, "submission_recovery_expired");
      }
      try {
        await this.runtime.ensure(record.spec.id);
        await this.store.confirmDispatch();
      } catch {
        // The Workflow MAY have started. Retrying the SAME key is the only safe retry.
        // Do not return 202 until the runtime acknowledges creation/reuse and the marker is durable.
        throw new Fault(503, "dispatch_unconfirmed_retry_same_key");
      }
    }
    // Once dispatched, a replay never calls ensure(), even after native history disappears.
    return { executionId: record.spec.id, reused: reservation.reused };
  }

  async inspect(caller: Principal): Promise<ExecutionView> {
    const record = await this.store.read();
    if (record === undefined) throw new Fault(404, "execution_not_found");
    assertVisible(record.spec, caller);
    const view: ExecutionView = {
      executionId: record.spec.id, organizationId: record.spec.organizationId, createdAt: record.spec.createdAt,
      dispatch: record.dispatched ? "confirmed" : "unconfirmed", runtimeStatus: null, output: null, error: null,
    };
    if (!record.dispatched) return view; // Read-only: status lookups must never launch work.
    let state: Awaited<ReturnType<WorkflowRuntime["status"]>>;
    try { state = await this.runtime.status(record.spec.id); }
    catch { throw new Fault(503, "runtime_status_unavailable"); }
    if (!STATES.has(state.status)) throw new Fault(502, "runtime_contract_error");
    view.runtimeStatus = state.status;
    if (state.status === "complete") {
      if (!object(state.output) || state.output.message !== record.spec.input.message) {
        throw new Fault(502, "runtime_contract_error");
      }
      view.output = { message: record.spec.input.message };
    }
    if (state.status === "errored") view.error = { code: "workflow_failed" };
    return view; // Do not expose arbitrary native error strings, stack traces, or output fields.
  }
}
