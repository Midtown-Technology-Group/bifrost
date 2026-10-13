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
		AreaChart: ({
			children,
			data,
		}: {
			children?: ReactNode;
			data?: unknown[];
		}) => (
			<div
				data-testid="memory-area-chart"
				data-points={data?.length ?? 0}
			>
				{children}
			</div>
		),
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

it("keeps distinct buckets that share the same display label", () => {
	useWorkerMetrics.mockReturnValue({
		data: {
			points: [
				{
					group: "Oct 13",
					bucket_start: "2026-10-13T01:00:00Z",
					worker_id: "worker-one",
					memory_current: 10,
					memory_max: 100,
				},
				{
					group: "Oct 13",
					bucket_start: "2026-10-13T01:30:00Z",
					worker_id: "worker-one",
					memory_current: 20,
					memory_max: 100,
				},
			],
		},
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});

	renderWithProviders(<MemoryChart />);
	expect(screen.getByTestId("memory-area-chart")).toHaveAttribute(
		"data-points",
		"2",
	);
});
