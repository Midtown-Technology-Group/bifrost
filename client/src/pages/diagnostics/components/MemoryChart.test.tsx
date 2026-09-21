import { beforeEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { renderWithProviders, screen } from "@/test-utils";
import { MemoryChart } from "./MemoryChart";

const useWorkerMetrics = vi.hoisted(() => vi.fn());
vi.mock("@/services/workers", () => ({ useWorkerMetrics }));
vi.mock("framer-motion", () => ({ useReducedMotion: () => false }));
vi.mock("recharts", () => {
	const Chart = ({ children }: { children?: ReactNode }) => (
		<div>{children}</div>
	);
	return {
		AreaChart: Chart,
		Area: () => null,
		XAxis: () => null,
		YAxis: () => null,
		CartesianGrid: () => null,
		Tooltip: () => null,
		ResponsiveContainer: Chart,
		ReferenceLine: () => null,
	};
});

beforeEach(() => {
	useWorkerMetrics.mockReturnValue({
		data: { points: [] },
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});
});

it("does not present unknown worker memory as zero or unlimited", () => {
	renderWithProviders(
		<MemoryChart
			livePools={[
				{
					worker_id: "worker-one",
					processes: [],
					memory_current_bytes: -1,
					memory_max_bytes: -1,
				} as never,
			]}
		/>,
	);
	expect(
		screen.getByText(
			"Memory telemetry unavailable for one or more workers",
		),
	).toBeInTheDocument();
	expect(
		screen.queryByText(/no memory limit reported/i),
	).not.toBeInTheDocument();
});

it("renders a known worker total and denominator", () => {
	renderWithProviders(
		<MemoryChart
			livePools={[
				{
					worker_id: "worker-one",
					processes: [],
					memory_current_bytes: 50 * 1024 * 1024,
					memory_max_bytes: 100 * 1024 * 1024,
				} as never,
			]}
		/>,
	);
	expect(screen.getByText("50 MB")).toBeInTheDocument();
	expect(screen.getByText(/100 MB across 1 container/)).toBeInTheDocument();
});

it("treats null and non-finite memory values as unknown", () => {
	renderWithProviders(
		<MemoryChart
			livePoints={[
				{
					group: "now",
					worker_id: "worker-one",
					memory_current: Number.NaN,
					memory_max: Number.POSITIVE_INFINITY,
				} as never,
			]}
		/>,
	);
	expect(
		screen.getByText(
			"Memory telemetry unavailable for one or more workers",
		),
	).toBeInTheDocument();
	expect(
		screen.queryByText(/NaN|Infinity|no memory limit reported/i),
	).not.toBeInTheDocument();
});

it("keeps historical invalid memory unknown after a valid latest sample", () => {
	renderWithProviders(
		<MemoryChart
			livePoints={
				[
					{
						group: "earlier",
						worker_id: "worker-one",
						memory_current: Number.NaN,
						memory_max: 100,
					},
					{
						group: "latest",
						worker_id: "worker-one",
						memory_current: 50,
						memory_max: 100,
					},
				] as never
			}
		/>,
	);
	expect(
		screen.getByText(
			"Memory telemetry unavailable for one or more workers",
		),
	).toBeInTheDocument();
});
