import { DurableObject, WorkflowEntrypoint } from "cloudflare:workers";
import type { WorkflowEvent, WorkflowStep } from "cloudflare:workers";
import { ExecutionCoordinator } from "./coordinator.js";
import type { AdmissionStore, ExecutionView, WorkflowRuntime } from "./coordinator.js";
import { capture, Fault } from "./errors.js";
import type { Outcome } from "./errors.js";
import { EXECUTION_ID, reserveDecision, WORKFLOW } from "./domain.js";
import type { Accepted, EchoInput, ExecutionRecord, ExecutionSpec } from "./domain.js";
import { handleRequest } from "./http.js";
import type { LabAuth } from "./http.js";
import type { Principal } from "./scope.js";

type WorkflowParams = { executionId: string };
interface Bindings extends LabAuth {
  EXECUTIONS: DurableObjectNamespace<ExecutionAdmission>;
  ECHO_WORKFLOW: Workflow<WorkflowParams>;
}
const RECORD_KEY = "admission-v1";

function admissionStore(storage: DurableObjectStorage): AdmissionStore {
  return {
    reserve: (spec) => storage.transaction(async (tx) => {
      const existing = await tx.get<ExecutionRecord>(RECORD_KEY);
      const reservation = reserveDecision(existing, spec);
      if (existing === undefined) await tx.put(RECORD_KEY, reservation.record);
      return reservation;
    }),
    confirmDispatch: () => storage.transaction(async (tx) => {
      const record = await tx.get<ExecutionRecord>(RECORD_KEY);
      if (record === undefined) throw new Error("admission_missing");
      if (!record.dispatched) await tx.put(RECORD_KEY, { ...record, dispatched: true });
    }),
    read: () => storage.get<ExecutionRecord>(RECORD_KEY),
  };
}

function workflowRuntime(binding: Workflow<WorkflowParams>): WorkflowRuntime {
  return {
    ensure: async (id) => {
      // Unlike create(), createBatch() skips an existing retained ID without an error.
      // A one-element batch removes exception-string matching from recovery logic.
      await binding.createBatch([{ id, params: { executionId: id } }]);
    },
    status: async (id) => (await binding.get(id)).status(),
  };
}

/** SQLite-backed immutable admission record; NOT a second execution state machine. */
export class ExecutionAdmission extends DurableObject<Bindings> {
  #coordinator(): ExecutionCoordinator {
    return new ExecutionCoordinator(admissionStore(this.ctx.storage), workflowRuntime(this.env.ECHO_WORKFLOW));
  }

  async submitExecution(spec: ExecutionSpec): Promise<Outcome<Accepted>> {
    return capture(async () => {
      if (this.env.LAB_ENABLED !== "true") throw new Fault(404, "not_found");
      if (!EXECUTION_ID.test(spec.id) ||
          this.env.EXECUTIONS.idFromName(spec.id).toString() !== this.ctx.id.toString()) {
        throw new Fault(400, "execution_key_mismatch");
      }
      return this.#coordinator().submit(spec);
    });
  }

  async inspectExecution(caller: Principal): Promise<Outcome<ExecutionView>> {
    return capture(async () => {
      if (this.env.LAB_ENABLED !== "true") throw new Fault(404, "not_found");
      return this.#coordinator().inspect(caller);
    });
  }

  // Internal binding-only RPC. No public route exposes immutable inputs without auth.
  async getRunInput(expectedId: string): Promise<Outcome<EchoInput>> {
    return capture(async () => {
      if (this.env.LAB_ENABLED !== "true") throw new Fault(404, "not_found");
      const record = await this.ctx.storage.get<ExecutionRecord>(RECORD_KEY);
      if (record === undefined || record.spec.id !== expectedId) throw new Fault(404, "execution_not_found");
      return record.spec.input;
    });
  }
}

export class EchoWorkflow extends WorkflowEntrypoint<Bindings, WorkflowParams> {
  async run(event: WorkflowEvent<WorkflowParams>, step: WorkflowStep): Promise<{ message: string }> {
    if (this.env.LAB_ENABLED !== "true") throw new Error("lab_disabled");
    const { executionId } = event.payload;
    if (!EXECUTION_ID.test(executionId) || executionId !== event.instanceId) {
      throw new Error("execution_key_mismatch");
    }
    const input = await step.do("load-immutable-input-v1", {
      retries: { limit: 2, delay: "1 second", backoff: "exponential" }, timeout: "10 seconds",
    }, async () => {
      const stub = this.env.EXECUTIONS.get(this.env.EXECUTIONS.idFromName(executionId));
      const result = await stub.getRunInput(executionId);
      if (!result.ok || result.value.workflow !== WORKFLOW) throw new Error("execution_input_unavailable");
      return result.value;
    });
    return step.do("echo-v1", {
      retries: { limit: 0, delay: "1 second" }, timeout: "10 seconds",
    }, async () => ({ message: input.message }));
  }
}

export default {
  fetch(request: Request, env: Bindings): Promise<Response> {
    return handleRequest(request, env, {
      submit: (spec) => env.EXECUTIONS.get(env.EXECUTIONS.idFromName(spec.id)).submitExecution(spec),
      inspect: (id, caller) => env.EXECUTIONS.get(env.EXECUTIONS.idFromName(id)).inspectExecution(caller),
    });
  },
} satisfies ExportedHandler<Bindings>;
