import { Fault } from "./errors.js";

export interface Principal {
  readonly userId: string;
  readonly organizationId: string | null;
  readonly isPlatformAdmin: boolean;
  readonly isProviderOrg: boolean;
}

/** Port of upstream api/shared/scope_resolver.py at 0598020e32ea6367ecda69f548a53c00bb77bb6c.
 * undefined = UNSET; null = an explicit request for global scope.
 * Callers must come from verified authentication, never from a request body.
 */
export function resolveScope(
  caller: Principal,
  requestedScope: string | null | undefined = undefined,
): string | null {
  if (requestedScope === undefined) return caller.organizationId;
  const bypass = caller.isPlatformAdmin || caller.isProviderOrg;
  if (requestedScope === null) {
    if (!bypass) throw new Fault(403, "scope_not_allowed");
    return null;
  }
  if (requestedScope === caller.organizationId) return requestedScope;
  if (!bypass) throw new Fault(403, "scope_not_allowed");
  return requestedScope;
}
