export class Fault extends Error {
  constructor(readonly status: number, readonly code: string) {
    super(code);
    this.name = "Fault";
  }
}

// Custom Error properties/prototypes must not be relied on across an RPC boundary.
export type Outcome<T> =
  | { ok: true; value: T }
  | { ok: false; fault: { status: number; code: string } };

export async function capture<T>(operation: () => Promise<T>): Promise<Outcome<T>> {
  try {
    return { ok: true, value: await operation() };
  } catch (error) {
    const fault = error instanceof Fault ? error : new Fault(500, "internal_error");
    return { ok: false, fault: { status: fault.status, code: fault.code } };
  }
}

export function unwrap<T>(result: Outcome<T>): T {
  if (!result.ok) throw new Fault(result.fault.status, result.fault.code);
  return result.value;
}
