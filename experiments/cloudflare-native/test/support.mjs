import { AdmissionService } from '../src/admission.ts';
import { runInventory } from '../src/orchestration.ts';

export const alice = { org_id: 'demo-org-a', subject: 'alice' };
export const bob = { org_id: 'demo-org-b', subject: 'bob' };
export const tokenA = 'local-test-token-a-never-deploy-0001';
export const tokenB = 'local-test-token-b-never-deploy-0002';
export const config = {
  LAB_ENABLED: 'true',
  LAB_PRINCIPALS: JSON.stringify({ [tokenA]: alice, [tokenB]: bob }),
};
export const payload = { workflow_id: 'lab.inventory.v1', input_data: { targets: ['demo-a', 'demo-b'] } };

/** In-memory contract double, NOT a Durable Object runtime or durability proof. */
export class AtomicStore {
  state;
  tail = Promise.resolve();
  failReceipt = false;
  async read() { await this.tail; return structuredClone(this.state); }
  transact(change) {
    const next = this.tail.then(() => {
      const update = change(structuredClone(this.state));
      if (update.record?.started && this.failReceipt) {
        this.failReceipt = false;
        throw new Error('simulated receipt write failure');
      }
      if (update.record) this.state = structuredClone(update.record);
      return update.result;
    });
    this.tail = next.catch(() => {});
    return next;
  }
}
export class FakeWorkflows {
  instances = new Map();
  createCalls = 0;
  statusCalls = 0;
  unavailable = false;
  loseCreateResponse = false;
  async create(command) {
    this.createCalls++;
    if (this.unavailable) throw new Error('simulated quota exceeded');
    if (this.instances.has(command.execution_id)) throw new Error('instance already exists');
    this.instances.set(command.execution_id, { status: 'queued', command: structuredClone(command) });
    if (this.loseCreateResponse) throw new Error('simulated lost create response');
  }
  async status(id) {
    this.statusCalls++;
    if (this.unavailable) throw new Error('simulated provider unavailable');
    const instance = this.instances.get(id);
    if (!instance) throw new Error('instance not found');
    return { status: instance.status, output: instance.output };
  }
  async complete(id) {
    const instance = this.instances.get(id);
    instance.output = await runInventory(instance.command, { run: (_name, action) => action() });
    instance.status = 'complete';
  }
}
export function system() {
  const workflows = new FakeWorkflows();
  const stores = new Map();
  const admission = (id) => {
    if (!stores.has(id)) stores.set(id, new AtomicStore());
    return new AdmissionService(stores.get(id), workflows);
  };
  return { workflows, stores, api: {
    submit: (command) => admission(command.execution_id).submit(command),
    inspect: (owner, id) => admission(id).inspect(owner, id),
  } };
}
export function post(body = payload, key = 'test-key', token = tokenA, extraHeaders = {}) {
  const headers = { 'Content-Type': 'application/json', ...extraHeaders };
  if (token !== null) headers.Authorization = `Bearer ${token}`;
  if (key !== null) headers['Idempotency-Key'] = key;
  return new Request('http://127.0.0.1/lab/executions', {
    method: 'POST', headers, body: typeof body === 'string' ? body : JSON.stringify(body),
  });
}
export function get(id, token = tokenA) {
  return new Request(`http://127.0.0.1/lab/executions/${id}`, { headers: { Authorization: `Bearer ${token}` } });
}
