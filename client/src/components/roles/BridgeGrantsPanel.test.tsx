import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { BridgeGrantsPanel } from "./BridgeGrantsPanel";

const state = vi.hoisted(() => ({
	mutate: vi.fn(),
	refetch: vi.fn(),
}));
vi.mock("@/hooks/useBridgeGrants", () => ({
	useBridgeGrants: () => ({
		data: {
			role_id: "role-1",
			is_builtin: false,
			permissions: ["devices.readwrite"],
			version: "0".repeat(64),
		},
		isError: false,
		isFetching: false,
		refetch: state.refetch,
	}),
	useUpdateBridgeGrants: () => ({ mutate: state.mutate, isPending: false }),
}));

beforeEach(() => vi.clearAllMocks());

describe("BridgeGrantsPanel", () => {
	it("saves only the typed bridge choices with the exact original set and version", async () => {
		const { user } = renderWithProviders(
			<BridgeGrantsPanel roleId="role-1" />,
		);
		await user.click(
			screen.getByRole("checkbox", { name: "Execute devices" }),
		);
		await user.click(
			screen.getByRole("button", {
				name: "Save device and agent grants",
			}),
		);
		expect(state.mutate).toHaveBeenCalledWith(
			{
				params: { path: { role_id: "role-1" } },
				body: {
					permissions: ["devices.readwrite", "devices.execute"],
					expected_permissions: ["devices.readwrite"],
					expected_version: "0".repeat(64),
				},
			},
			expect.objectContaining({
				onSuccess: expect.any(Function),
				onError: expect.any(Function),
			}),
		);
	});

	it("reload discards unsaved grant changes", async () => {
		const { user } = renderWithProviders(
			<BridgeGrantsPanel roleId="role-1" />,
		);
		await user.click(
			screen.getByRole("checkbox", { name: "Execute devices" }),
		);
		await user.click(screen.getByRole("button", { name: "Reload grants" }));
		expect(state.refetch).toHaveBeenCalledOnce();
		expect(
			screen.getByRole("checkbox", { name: "Execute devices" }),
		).not.toBeChecked();
		expect(
			screen.getByRole("button", {
				name: "Save device and agent grants",
			}),
		).toBeDisabled();
	});
});
