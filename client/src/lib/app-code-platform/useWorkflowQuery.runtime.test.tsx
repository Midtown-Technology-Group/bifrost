import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

vi.mock("@/lib/api-client", () => ({
	apiClient: { POST: vi.fn() },
}));
vi.mock("@/hooks/useExecutions", () => ({ getExecution: vi.fn() }));
vi.mock("@/lib/app-sdk/execution-stream", () => ({
	subscribeToExecution: vi.fn(),
}));

import { apiClient } from "@/lib/api-client";
import { useWorkflowQuery } from "./useWorkflowQuery";

interface SetupStatus {
	microsoft_csp: { connected: boolean };
	microsoft: { connected: boolean };
	ready_for_consent: boolean;
}

const CONNECTED: SetupStatus = {
	microsoft_csp: { connected: true },
	microsoft: { connected: true },
	ready_for_consent: true,
};

/**
 * The setup-status cards are driven entirely by this hook's reactive state.
 * A transport-level rejection (e.g. an app request issued while the token is
 * being rotated) must not strand the hook in "loading, no data" — the card
 * would render its empty branch forever, even after a direct execution of the
 * same workflow reports both integrations connected.
 */
describe("useWorkflowQuery setup-status state", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("settles an execution rejection into an error state instead of loading forever", async () => {
		(apiClient.POST as Mock).mockRejectedValue(
			new Error("Authentication required"),
		);

		const { result } = renderHook(() =>
			useWorkflowQuery<SetupStatus>("check_microsoft_setup"),
		);

		await waitFor(() => {
			expect(result.current.isError).toBe(true);
		});
		expect(result.current.errorMessage).toBe("Authentication required");
		expect(result.current.isLoading).toBe(false);
		expect(result.current.data).toBeNull();
	});

	it("shows the successful workflow result once a retry succeeds", async () => {
		(apiClient.POST as Mock)
			.mockRejectedValueOnce(new Error("Authentication required"))
			.mockResolvedValueOnce({
				data: {
					execution_id: "exec-setup-1",
					status: "Success",
					is_transient: true,
					result: CONNECTED,
				},
				error: undefined,
			});

		const { result } = renderHook(() =>
			useWorkflowQuery<SetupStatus>("check_microsoft_setup"),
		);

		await waitFor(() => {
			expect(result.current.isError).toBe(true);
		});
		expect(result.current.isLoading).toBe(false);
		expect(result.current.data).toBeNull();

		await act(async () => {
			await result.current.refetch();
		});

		await waitFor(() => {
			expect(result.current.data).toEqual(CONNECTED);
		});
		expect(result.current.isError).toBe(false);
		expect(result.current.isLoading).toBe(false);
	});
});
