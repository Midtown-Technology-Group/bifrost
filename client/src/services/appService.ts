import { useQuery } from "@tanstack/react-query";
import { authFetch } from "@/lib/api-client";

export const APP_SERVICE_RANGES = ["1h", "6h", "24h", "7d"] as const;
export type AppServiceRange = (typeof APP_SERVICE_RANGES)[number];

export interface AppServiceMetricPoint {
	timestamp: string;
	value: number | null;
}

export interface AppServiceMetricSeries {
	name: "CpuPercentage" | "MemoryPercentage" | "HttpQueueLength";
	unit: "Percent" | "Count";
	aggregation: "Average";
	series_label: "plan average";
	latest_sample_at: string | null;
	stale: boolean;
	stale_reason: string | null;
	available: boolean;
	unavailable_reason: string | null;
	points: AppServiceMetricPoint[];
}

export interface AppServiceMetricsResponse {
	source: "azure_monitor";
	scope: "app_service_plan";
	resource_id: string | null;
	range: AppServiceRange;
	sample_grain: string;
	fetched_at: string;
	latest_sample_at: string | null;
	stale: boolean;
	stale_reason: string | null;
	status: "available" | "unavailable";
	unavailable_reason: string | null;
	metrics: AppServiceMetricSeries[];
}

export function useAppServiceMetrics(range: AppServiceRange = "1h") {
	return useQuery<AppServiceMetricsResponse>({
		queryKey: ["app-service-metrics", range],
		queryFn: async () => {
			const response = await authFetch(
				`/api/platform/app-service/metrics?range=${range}`,
			);
			if (!response.ok) {
				throw new Error(
					`Failed to fetch App Service metrics: ${response.statusText}`,
				);
			}
			return (await response.json()) as AppServiceMetricsResponse;
		},
		staleTime: 60_000,
		refetchInterval: 60_000,
	});
}
