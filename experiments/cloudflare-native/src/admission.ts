import {
  ADMISSION_RETRY_WINDOW_MS, samePrincipal,
  type ExecutionCommand, type Principal, type RuntimeStatus,
} from "./contracts.ts";

export type AdmissionRecord = {
  command: ExecutionCommand;
  created_at: number;
  started: boolean;
};
export interface AdmissionStore {
  read(): Promise<AdmissionRecord | undefined>;
  // The callback MUST run atomically against durable storage. No external I/O inside it.
  transact<T>(change: (record: AdmissionRecord | undefined) => {
    record?: AdmissionRecord; result: T;
  }): Promise<T>;
}
export interface WorkflowPort {
  create(command: ExecutionCommand): Promise<void>;
  status(executionId: string): Promise<RuntimeStatus>;
}
export type SubmitResult =
  | { kind: "accepted"; execution_id: string; replayed: boolean }
  | { kind: "conflict" }
  | { kind: "expired" };
export type InspectResult =
  | { kind: "found"; runtime: RuntimeStatus }
  | { kind: "not_found" };

/** The record owns admission only. Cloudflare Workflows owns execution lifecycle. */
export class AdmissionService {
  private readonly store: AdmissionStore;
  private readonly workflows: WorkflowPort;
  private readonly now: () => number;
  constructor(store: AdmissionStore, workflows: WorkflowPort, now = Date.now) {
    this.store = store;
    this.workflows = workflows;
    this.now = now;
  }
  async submit(command: ExecutionCommand): Promise<SubmitResult> {
    const reservation = await this.store.transact((existing) => {
      if (existing) {
        const original = existing.command;
        const conflict = original.execution_id !== command.execution_id ||
          original.fingerprint !== command.fingerprint ||
          !samePrincipal(original.owner, command.owner);
        return { result: { conflict, replayed: true, record: existing } };
      }
      const record = { command, created_at: this.now(), started: false };
      return { record, result: { conflict: false, replayed: false, record } };
    });
    if (reservation.conflict) return { kind: "conflict" };
    const { record } = reservation;
    if (!record.started) {
      // Never silently resurrect a very old ambiguous launch after Workflow retention.
      if (this.now() - record.created_at >= ADMISSION_RETRY_WINDOW_MS) {
        return { kind: "expired" };
      }
      try {
        await this.workflows.create(record.command);
      } catch {
        // A create failure could be a quota failure OR a lost successful response.
        // Only a successful status lookup proves an instance actually exists.
        // Propagate lookup failure: an error is not evidence of successful admission.
        const observed = await this.workflows.status(command.execution_id);
        if (!["queued", "running", "waiting", "paused", "waitingForPause", "complete", "errored", "terminated"].includes(observed.status)) {
          throw new Error("Workflow launch could not be confirmed");
        }
      }
      await this.store.transact((current) => {
        if (!current) throw new Error("Admission record disappeared");
        return { record: { ...current, started: true }, result: undefined };
      });
    }
    return { kind: "accepted", execution_id: command.execution_id, replayed: reservation.replayed };
  }
  async inspect(owner: Principal, executionId: string): Promise<InspectResult> {
    const record = await this.store.read();
    if (!record || record.command.execution_id !== executionId ||
        !samePrincipal(record.command.owner, owner)) return { kind: "not_found" };
    // Authorization happens BEFORE touching the Workflow binding.
    return { kind: "found", runtime: await this.workflows.status(executionId) };
  }
}
