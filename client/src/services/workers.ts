/**
 * Workers API service
 *
 * Provides hooks for:
 * - Listing process pools and their status
 * - Getting pool details
 * - Recycling worker processes
 * - Queue status
 * - Pool statistics
 *
 * Response types come from the generated OpenAPI client (`@/lib/v1`,
 * refreshed with `npm run generate:types` from a running stack). The hooks
 * below keep using `authFetch` because these endpoints use path/query shapes
 * that predate the generated `$api` wrappers, not because the schemas are
 * missing from generation.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";

// =============================================================================
// Generated Type Aliases (api/src/models/contracts/platform.py)
// =============================================================================

// Process states from ProcessPoolManager: idle, busy, killed
export type ProcessState = components["schemas"]["ProcessInfo"]["state"];

export type ProcessInfo = components["schemas"]["ProcessInfo"] & {
	/**
	 * WebSocket-only enrichment from the worker heartbeat. This field is not
	 * part of the REST DTO; it is attached by `useWorkerWebSocket` when
	 * converting heartbeat processes.
	 */
	pending_recycle?: boolean;
};

export type PoolSummary = components["schemas"]["PoolSummary"];
export type PoolDetail = components["schemas"]["PoolDetail"];
export type PoolsListResponse = components["schemas"]["PoolsListResponse"];
export type PoolStatsResponse = components["schemas"]["PoolStatsResponse"];
export type QueueItem = components["schemas"]["QueueItem"];
export type QueueStatusResponse = components["schemas"]["QueueStatusResponse"];
export type RecycleRequest =
	components["schemas"]["RecycleProcessRequest"];
export type RecycleResponse =
	components["schemas"]["RecycleProcessResponse"];
export type RecycleAllRequest = components["schemas"]["RecycleAllRequest"];
export type RecycleAllResponse =
	components["schemas"]["RecycleAllResponse"];

// =============================================================================
// Pool Hooks
// =============================================================================

/**
 * Hook to fetch all process pools
 */
export function usePools() {
	return useQuery<PoolsListResponse>({
		queryKey: ["pools"],
		queryFn: async () => {
			const response = await authFetch("/api/platform/workers");
			if (!response.ok) {
				throw new Error(`Failed to fetch pools: ${response.statusText}`);
			}
			return response.json();
		},
		// No polling - real-time updates come via WebSocket (useWorkerWebSocket)
	});
}

/**
 * Hook to fetch a single pool's details
 */
export function usePool(workerId: string) {
	return useQuery<PoolDetail>({
		queryKey: ["pools", workerId],
		queryFn: async () => {
			const response = await authFetch(`/api/platform/workers/${workerId}`);
			if (!response.ok) {
				throw new Error(`Failed to fetch pool: ${response.statusText}`);
			}
			return response.json();
		},
		enabled: !!workerId,
	});
}

/**
 * Hook to fetch pool statistics
 */
export function usePoolStats() {
	return useQuery<PoolStatsResponse>({
		queryKey: ["pools", "stats"],
		queryFn: async () => {
			const response = await authFetch("/api/platform/workers/stats");
			if (!response.ok) {
				throw new Error(`Failed to fetch pool stats: ${response.statusText}`);
			}
			return response.json();
		},
		// No polling - real-time updates come via WebSocket (useWorkerWebSocket)
	});
}

/**
 * Hook to recycle a process in a pool
 */
export function useRecycleProcess() {
	const queryClient = useQueryClient();

	return useMutation<
		RecycleResponse,
		Error,
		{ params: { path: { worker_id: string; pid: number } }; body: RecycleRequest }
	>({
		mutationFn: async ({ params, body }) => {
			const { worker_id, pid } = params.path;
			const response = await authFetch(
				`/api/platform/workers/${worker_id}/processes/${pid}/recycle`,
				{
					method: "POST",
					body: JSON.stringify(body),
				}
			);
			if (!response.ok) {
				throw new Error(`Failed to recycle process: ${response.statusText}`);
			}
			return response.json();
		},
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ["pools"] });
		},
	});
}

// =============================================================================
// Queue Hooks
// =============================================================================

/**
 * Hook to fetch queue status
 */
export function useQueueStatus(params?: { limit?: number; offset?: number }) {
	return useQuery<QueueStatusResponse>({
		queryKey: ["queue", params],
		queryFn: async () => {
			const searchParams = new URLSearchParams();
			if (params?.limit) searchParams.set("limit", String(params.limit));
			if (params?.offset) searchParams.set("offset", String(params.offset));

			const url = `/api/platform/queue${searchParams.toString() ? `?${searchParams}` : ""}`;
			const response = await authFetch(url);
			if (!response.ok) {
				throw new Error(`Failed to fetch queue: ${response.statusText}`);
			}
			return response.json();
		},
		// No polling - real-time updates come via WebSocket (useWorkerWebSocket)
	});
}

/**
 * Hook to recycle all processes in a pool
 */
export function useRecycleAllProcesses() {
	const queryClient = useQueryClient();

	return useMutation<
		RecycleAllResponse,
		Error,
		{ workerId: string; reason?: string }
	>({
		mutationFn: async ({ workerId, reason }) => {
			const response = await authFetch(
				`/api/platform/workers/${workerId}/recycle-all`,
				{
					method: "POST",
					body: JSON.stringify({ reason }),
				}
			);
			if (!response.ok) {
				const error = await response.json().catch(() => ({}));
				throw new Error(
					error.detail || `Failed to recycle: ${response.statusText}`
				);
			}
			return response.json();
		},
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ["pools"] });
		},
	});
}

// =============================================================================
// Worker Metrics (Time-Series for Diagnostics Chart)
// =============================================================================

export type WorkerMetricPoint =
	components["schemas"]["WorkerMetricPoint"];
export type WorkerMetricsResponse =
	components["schemas"]["WorkerMetricsResponse"];

export function useWorkerMetrics(range: string = "1h") {
    return useQuery<WorkerMetricsResponse>({
        queryKey: ["worker-metrics", range],
        queryFn: async () => {
            const response = await authFetch(
                `/api/platform/workers/metrics?range=${range}`
            );
            if (!response.ok) {
                throw new Error(
                    `Failed to fetch worker metrics: ${response.statusText}`
                );
            }
            return response.json();
        },
        refetchInterval: 60_000, // Refresh every 60s to get new data points
    });
}
