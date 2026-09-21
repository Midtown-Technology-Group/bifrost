import { useMemo, useState } from "react";
import {
	CartesianGrid,
	Line,
	LineChart,
	ResponsiveContainer,
	Tooltip,
	XAxis,
	YAxis,
} from "recharts";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
	APP_SERVICE_RANGES,
	type AppServiceMetricSeries,
	type AppServiceRange,
	useAppServiceMetrics,
} from "@/services/appService";

const METRIC_LABELS = {
	CpuPercentage: "CPU",
	MemoryPercentage: "Memory",
	HttpQueueLength: "HTTP queue",
} as const;

type ChartPoint = {
	timestamp: string;
	cpu: number | null;
	memory: number | null;
	queue: number | null;
};

function latestValue(metric: AppServiceMetricSeries) {
	return [...metric.points].reverse().find((point) => point.value != null)
		?.value;
}

function formatValue(metric: AppServiceMetricSeries) {
	const value = latestValue(metric);
	if (value == null) return "Unavailable";
	if (metric.unit === "Percent") return `${value.toFixed(1)} %`;
	if (value > 0 && value < 0.01) return "<0.01 requests";
	let decimals = 0;
	if ((value !== 0 && Math.abs(value) < 1) || value % 1) decimals = 2;
	return `${value.toFixed(decimals)} requests`;
}

function formatTimestamp(value: string | null) {
	if (!value) return "No sample received";
	return new Date(value)
		.toISOString()
		.replace("T", " ")
		.replace(/\.\d{3}Z$/, " UTC");
}

function metricStatus(metric: AppServiceMetricSeries) {
	if (metric.stale)
		return `Stale · latest ${formatTimestamp(metric.latest_sample_at)}`;
	if (metric.available) {
		return `Latest plan average · ${formatTimestamp(metric.latest_sample_at)}`;
	}
	return "No usable sample";
}

function chartData(metrics: AppServiceMetricSeries[]): ChartPoint[] {
	const points = new Map<string, ChartPoint>();
	for (const metric of metrics) {
		for (const point of metric.points) {
			if (!point.timestamp) continue;
			const current = points.get(point.timestamp) ?? {
				timestamp: point.timestamp,
				cpu: null,
				memory: null,
				queue: null,
			};
			if (metric.name === "CpuPercentage") current.cpu = point.value;
			if (metric.name === "MemoryPercentage")
				current.memory = point.value;
			if (metric.name === "HttpQueueLength") current.queue = point.value;
			points.set(point.timestamp, current);
		}
	}
	return [...points.values()].sort((left, right) =>
		left.timestamp.localeCompare(right.timestamp),
	);
}

function CapacityTooltip({
	active,
	payload,
	label,
}: Readonly<{
	active?: boolean;
	payload?: Array<{ name: string; value: number | null }>;
	label?: string;
}>) {
	if (!active || !payload?.length) return null;
	return (
		<div className="rounded-md border bg-background p-2 text-xs shadow-sm">
			<div className="mb-1 text-muted-foreground">
				{formatTimestamp(label ?? null)}
			</div>
			{payload.map((entry) => (
				<div key={entry.name}>
					{entry.name}: {entry.value ?? "Unavailable"}
				</div>
			))}
		</div>
	);
}

export function AppServicePanel() {
	const [range, setRange] = useState<AppServiceRange>("1h");
	const { data, isLoading, isError, isFetching, refetch } =
		useAppServiceMetrics(range);
	const points = useMemo(
		() => chartData(data?.metrics ?? []),
		[data?.metrics],
	);

	if (isLoading) {
		return (
			<Card data-testid="app-service-panel">
				<CardContent className="space-y-4 pt-6">
					<Skeleton className="h-5 w-48" />
					<Skeleton className="h-24 w-full" />
				</CardContent>
			</Card>
		);
	}

	const unavailable = isError || data?.status === "unavailable";
	const partial =
		data?.status === "available" &&
		data.unavailable_reason?.startsWith("missing:");
	let unavailableDescription =
		"Azure Monitor returned no usable metric samples.";
	if (isError) unavailableDescription = "Azure Monitor could not be reached.";
	else if (data?.unavailable_reason === "not_configured") {
		unavailableDescription =
			"This environment has no App Service plan configured.";
	}
	return (
		<Card data-testid="app-service-panel">
			<CardHeader className="gap-3 sm:flex-row sm:items-center sm:justify-between">
				<div>
					<CardTitle>App Service plan capacity</CardTitle>
					<p className="text-sm text-muted-foreground">
						Azure Monitor · plan average · UTC samples
					</p>
				</div>
				<div
					className="flex gap-1"
					aria-label="App Service metric range"
					role="group"
				>
					{APP_SERVICE_RANGES.map((option) => (
						<Button
							key={option}
							type="button"
							size="sm"
							variant={range === option ? "default" : "ghost"}
							aria-pressed={range === option}
							onClick={() => setRange(option)}
						>
							{option}
						</Button>
					))}
				</div>
			</CardHeader>
			<CardContent className="space-y-4">
				{unavailable && (
					<Alert variant="destructive">
						<AlertTitle>App Service metrics unavailable</AlertTitle>
						<AlertDescription>
							{unavailableDescription}
						</AlertDescription>
						<Button
							type="button"
							variant="outline"
							className="mt-3"
							disabled={isFetching}
							onClick={() => void refetch()}
						>
							{isFetching ? "Retrying…" : "Retry"}
						</Button>
					</Alert>
				)}
				{partial && (
					<Alert className="border-[var(--bf-warning)]/30">
						<AlertTitle>
							Some App Service metrics unavailable
						</AlertTitle>
						<AlertDescription>
							Azure Monitor returned usable data for part of this
							range; missing metrics remain unavailable.
						</AlertDescription>
					</Alert>
				)}
				{data?.stale && (
					<Alert className="border-[var(--bf-warning)]/30">
						<AlertTitle>
							Showing stale Azure Monitor data
						</AlertTitle>
						<AlertDescription>
							The latest usable sample is from{" "}
							{formatTimestamp(data.latest_sample_at)}.
						</AlertDescription>
					</Alert>
				)}
				<div className="grid gap-3 sm:grid-cols-3">
					{(data?.metrics ?? []).map((metric) => (
						<div
							key={metric.name}
							className="rounded-md border p-4"
							aria-label={METRIC_LABELS[metric.name]}
						>
							<div className="text-xs uppercase tracking-wider text-muted-foreground">
								{METRIC_LABELS[metric.name]}
							</div>
							<div className="mt-2 text-2xl font-semibold">
								{formatValue(metric)}
							</div>
							<div className="mt-1 text-xs text-muted-foreground">
								{metricStatus(metric)}
							</div>
						</div>
					))}
				</div>
				{points.length > 0 && (
					<div className="grid gap-4 lg:grid-cols-2">
						<div className="rounded-md border p-3">
							<h3 className="mb-2 text-sm font-medium">
								CPU and Memory (%)
							</h3>
							<div
								className="h-56"
								aria-label="CPU and Memory capacity chart"
								role="img"
							>
								<ResponsiveContainer width="100%" height="100%">
									<LineChart data={points}>
										<CartesianGrid strokeDasharray="3 3" />
										<XAxis dataKey="timestamp" hide />
										<YAxis domain={[0, 100]} unit="%" />
										<Tooltip
											content={<CapacityTooltip />}
										/>
										<Line
											type="monotone"
											dataKey="cpu"
											name="CPU"
											stroke="#2563eb"
											dot={false}
											connectNulls={false}
										/>
										<Line
											type="monotone"
											dataKey="memory"
											name="Memory"
											stroke="#10b981"
											dot={false}
											connectNulls={false}
										/>
									</LineChart>
								</ResponsiveContainer>
							</div>
						</div>
						<div className="rounded-md border p-3">
							<h3 className="mb-2 text-sm font-medium">
								HTTP queue (requests)
							</h3>
							<div
								className="h-56"
								aria-label="HTTP queue capacity chart"
								role="img"
							>
								<ResponsiveContainer width="100%" height="100%">
									<LineChart data={points}>
										<CartesianGrid strokeDasharray="3 3" />
										<XAxis dataKey="timestamp" hide />
										<YAxis />
										<Tooltip
											content={<CapacityTooltip />}
										/>
										<Line
											type="monotone"
											dataKey="queue"
											name="HTTP queue"
											stroke="#f59e0b"
											dot={false}
											connectNulls={false}
										/>
									</LineChart>
								</ResponsiveContainer>
							</div>
						</div>
					</div>
				)}
				{data && (
					<p className="text-xs text-muted-foreground">
						Fetched {formatTimestamp(data.fetched_at)} · latest
						sample {formatTimestamp(data.latest_sample_at)} · grain{" "}
						{data.sample_grain}
					</p>
				)}
			</CardContent>
		</Card>
	);
}
