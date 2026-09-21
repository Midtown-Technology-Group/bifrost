import { beforeEach, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { AppServicePanel } from "./AppServicePanel";

const useAppServiceMetrics = vi.hoisted(() => vi.fn());
vi.mock("@/services/appService", () => ({
	APP_SERVICE_RANGES: ["1h", "6h", "24h", "7d"],
	useAppServiceMetrics,
}));

beforeEach(() => {
	useAppServiceMetrics.mockReturnValue({
		data: {
			status: "available",
			stale: false,
			fetched_at: "2026-09-21T14:00:00Z",
			latest_sample_at: "2026-09-21T13:59:00Z",
			sample_grain: "PT1M",
			metrics: [
				{
					name: "CpuPercentage",
					unit: "Percent",
					available: true,
					stale: false,
					latest_sample_at: "2026-09-21T13:59:00Z",
					points: [
						{ timestamp: "2026-09-21T13:58:00Z", value: 42.5 },
						{ timestamp: "2026-09-21T13:59:00Z", value: null },
					],
				},
				{
					name: "MemoryPercentage",
					unit: "Percent",
					available: false,
					stale: false,
					latest_sample_at: null,
					points: [{ timestamp: "2026-09-21T13:59:00Z", value: null }],
				},
				{
					name: "HttpQueueLength",
					unit: "Count",
					available: true,
					stale: false,
					latest_sample_at: "2026-09-21T13:59:00Z",
					points: [
						{ timestamp: "2026-09-21T13:58:00Z", value: 0.25 },
						{ timestamp: "2026-09-21T13:59:00Z", value: 0.004 },
					],
				},
			],
		},
	});
});

it("renders populated values and leaves missing metrics unavailable", () => {
	renderWithProviders(<AppServicePanel />);
	expect(screen.getByText("42.5 %")).toBeInTheDocument();
	expect(screen.getByText("Unavailable")).toBeInTheDocument();
	expect(screen.getByText("<0.01 requests")).toBeInTheDocument();
	expect(screen.getByRole("heading", { name: "CPU and Memory (%)" })).toBeInTheDocument();
	expect(screen.getByRole("heading", { name: "HTTP queue (requests)" })).toBeInTheDocument();
	expect(screen.getAllByText(/2026-09-21 13:59:00 UTC/).length).toBeGreaterThan(0);
});

it("explains an unconfigured plan without implying zero capacity", () => {
	useAppServiceMetrics.mockReturnValue({
		data: { status: "unavailable", unavailable_reason: "not_configured", metrics: [] },
	});
	renderWithProviders(<AppServicePanel />);
	expect(screen.getByText("App Service metrics unavailable")).toBeInTheDocument();
	expect(screen.getByText(/no App Service plan configured/i)).toBeInTheDocument();
});
