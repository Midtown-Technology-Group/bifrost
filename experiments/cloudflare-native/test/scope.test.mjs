import test from "node:test";
import assert from "node:assert/strict";
import { resolveScope } from "../.build/scope.js";
import { principal, ORG, OTHER_ORG } from "./helpers.mjs";

// Explicit oracle derived from upstream's four-rule table, not from the implementation.
for (const defaultOrg of [ORG, null]) {
  for (const [isPlatformAdmin, isProviderOrg] of [[false, false], [true, false], [false, true], [true, true]]) {
    const caller = { ...principal, organizationId: defaultOrg, isPlatformAdmin, isProviderOrg };
    const privileged = isPlatformAdmin || isProviderOrg;
    for (const [label, requested, allowed, expected] of [
      ["omitted", undefined, true, defaultOrg],
      ["explicit-global", null, privileged, null],
      ["org-A", ORG, defaultOrg === ORG || privileged, ORG],
      ["org-B", OTHER_ORG, privileged, OTHER_ORG],
    ]) {
      test(`scope ${defaultOrg ?? "no-org"}: admin=${isPlatformAdmin} provider=${isProviderOrg} ${label}`, () => {
        if (allowed) assert.equal(resolveScope(caller, requested), expected);
        else assert.throws(() => resolveScope(caller, requested), { status: 403, code: "scope_not_allowed" });
      });
    }
  }
}
