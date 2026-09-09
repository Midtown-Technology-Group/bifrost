import { DurableObject, WorkflowEntrypoint, type WorkflowEvent, type WorkflowStep } from "cloudflare:workers";
import { NonRetryableError } from "cloudflare:workflows";
import { AdmissionService, type AdmissionRecord, type AdmissionStore } from "./admission.ts";
import { EXECUTION_ID_PATTERN, isPrincipal, type ExecutionCommand, type Principal } from "./contracts.ts";
import { handleRequest, type LabConfig } from "./http.ts";
import { runInventory } from "./orchestration.ts";

interface Env extends LabConfig {
  ADMISSIONS: DurableObjectNamespace<ExecutionAdmission>;
  EXECUTIONS: Workflow<ExecutionCommand>;
}
export class ExecutionAdmission extends DurableObject<Env> {
  private readonly service: AdmissionService;
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    const store: AdmissionStore = {
      read: () => ctx.storage.get<AdmissionRecord>("admission"),
      transact: (change) => ctx.storage.transaction(async (transaction) => {
        const update = change(await transaction.get<AdmissionRecord>("admission"));
        if (update.record) await transaction.put("admission", update.record);
        return update.result;
      }),
    };
    this.service = new AdmissionService(store, {
      create: async (command) => {
        await env.EXECUTIONS.create({ id: command.execution_id, params: command });
      },
      status: async (id) => (await env.EXECUTIONS.get(id)).status(),
    });
  }
  submit(command: ExecutionCommand) { return this.service.submit(command); }
  inspect(owner: Principal, id: string) { return this.service.inspect(owner, id); }
}
export class InventoryWorkflow extends WorkflowEntrypoint<Env, ExecutionCommand> {
  async run(event: WorkflowEvent<ExecutionCommand>, step: WorkflowStep) {
    const command = event.payload;
    if (!command || command.version !== 1 || !EXECUTION_ID_PATTERN.test(command.execution_id) ||
        command.execution_id !== event.instanceId || !isPrincipal(command.owner)) {
      throw new NonRetryableError("Invalid lab command");
    }
    return runInventory(command, {
      run: (name, action) => step.do(name, {
        retries: { limit: 2, delay: "1 second", backoff: "exponential" },
        timeout: "10 seconds",
      }, action),
    });
  }
}
export default {
  fetch(request: Request, env: Env): Promise<Response> {
    const admission = (id: string) => env.ADMISSIONS.get(env.ADMISSIONS.idFromName(id));
    return handleRequest(request, env, {
      submit: (command) => admission(command.execution_id).submit(command),
      inspect: (owner, id) => admission(id).inspect(owner, id),
    });
  },
} satisfies ExportedHandler<Env>;
