import { describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useAppServiceMetrics } from "./appService";

const authFetch = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api-client", () => ({ authFetch }));

describe("useAppServiceMetrics", () => {
	it("requests the bounded server endpoint and preserves null gaps", async () => {
		authFetch.mockResolvedValueOnce({
			ok: true,
			json: async () => ({
				status: "available",
				metrics: [{ name: "CpuPercentage", points: [{ value: null }] }],
			}),
		});
		const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
		const { result } = renderHook(() => useAppServiceMetrics("6h"), {
			wrapper: ({ children }) => (
				<QueryClientProvider client={client}>{children}</QueryClientProvider>
			),
		});
		await waitFor(() => expect(result.current.data?.status).toBe("available"));
		expect(authFetch).toHaveBeenCalledWith(
			"/api/platform/app-service/metrics?range=6h",
		);
		expect(result.current.data?.metrics[0].points[0].value).toBeNull();
	});
});
