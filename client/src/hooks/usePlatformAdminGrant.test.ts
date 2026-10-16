import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const mockAuth = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockAuth(),
}));

import { usePlatformAdminGrant } from "./usePlatformAdminGrant";

const cases: Array<{
	isPlatformAdmin: boolean;
	roles: string[];
	expected: boolean;
}> = [
	{ isPlatformAdmin: true, roles: [], expected: true },
	{ isPlatformAdmin: false, roles: ["Platform Admin"], expected: true },
	{ isPlatformAdmin: false, roles: ["Platform Owner"], expected: true },
	{ isPlatformAdmin: false, roles: ["Org Admin"], expected: false },
];

describe("usePlatformAdminGrant", () => {
	it.each(cases)(
		"maps superuser=$isPlatformAdmin roles=$roles to $expected",
		({ isPlatformAdmin, roles, expected }) => {
			mockAuth.mockReturnValue({
				isPlatformAdmin,
				hasRole: (role: string) => roles.includes(role),
			});

			const { result } = renderHook(() => usePlatformAdminGrant());

			expect(result.current).toBe(expected);
		},
	);
});
