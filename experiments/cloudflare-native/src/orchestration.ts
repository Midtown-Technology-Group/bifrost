import { parseExecutionRequest, type ExecutionCommand, type Principal } from "./contracts.ts";

export type TargetResult = { target: string; simulated: true; operation_id: string };
export type ExecutionResult = { execution_id: string; results: TargetResult[] };
export interface Executor {
  /** Real implementations MUST enforce this operation_id at the side-effect boundary. */
  execute(input: { owner: Principal; target: string; operation_id: string }): Promise<TargetResult>;
}
export interface StepRunner {
  run(name: string, action: () => Promise<TargetResult>): Promise<TargetResult>;
}
export const syntheticExecutor: Executor = {
  async execute({ target, operation_id }) {
    // Deliberately no fetch, credentials, subprocesses, or contact with real devices.
    return { target, simulated: true, operation_id };
  },
};
export async function runInventory(
  command: ExecutionCommand, steps: StepRunner, executor: Executor = syntheticExecutor,
): Promise<ExecutionResult> {
  const request = parseExecutionRequest(command.request);
  const results: TargetResult[] = [];
  // Serial and bounded for the first experiment. Workflow steps own retries.
  for (const [index, target] of request.input_data.targets.entries()) {
    const operation_id = `${command.execution_id}:inventory-v1:${index}`;
    const result = await steps.run(`inventory-v1-${index}`, () => executor.execute({
      owner: command.owner, target, operation_id,
    }));
    results.push(result);
  }
  return { execution_id: command.execution_id, results };
}
