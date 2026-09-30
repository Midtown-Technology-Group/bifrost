import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PoolMessage } from "@/services/websocket";
import type { PoolSummary } from "@/services/workers";
import { getPoolCounts } from "../components/ContainerTable";
import { useWorkerWebSocket } from "./useWorkerWebSocket";

const socket = vi.hoisted(() => ({
	connected: true,
	listener: undefined as ((connected: boolean) => void) | undefined,
	poolListener: undefined as ((message: PoolMessage) => void) | undefined,
	stopStatus: vi.fn(),
	stopMessages: vi.fn(),
	unsubscribe: vi.fn(),
}));
vi.mock("@/services/websocket", () => ({
	webSocketService: {
		connect: vi.fn().mockResolvedValue(undefined),
		isConnected: () => socket.connected,
		onConnectionStatusChange: (listener: (connected: boolean) => void) => {
			socket.listener = listener;
			return socket.stopStatus;
		},
		onPoolMessage: (listener: (message: PoolMessage) => void) => {
			socket.poolListener = listener;
			return socket.stopMessages;
		},
		unsubscribe: socket.unsubscribe,
	},
}));

const restSummary: PoolSummary = {
	worker_id: "worker-one",
	hostname: "worker-one",
	status: "online",
	started_at: null,
	pool_size: 3,
	active_process_count: 3,
	configured_capacity: 6,
	max_workers: 6,
	idle_count: 2,
	busy_count: 1,
	last_heartbeat: null,
};

/** WorkersTab merges the REST summary under the WS pool for each worker. */
function mergeWithRest(wsPool: object) {
	return { ...restSummary, ...wsPool };
}

describe("useWorkerWebSocket connection state", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		socket.connected = true;
	});
	it("tracks disconnection and reconnection after the initial subscription and cleans up", async () => {
		const { result, unmount } = renderHook(() => useWorkerWebSocket());
		await waitFor(() => expect(result.current.isConnected).toBe(true));
		act(() => {
			socket.connected = false;
			socket.listener?.(false);
		});
		expect(result.current.isConnected).toBe(false);
		act(() => {
			socket.connected = true;
			socket.listener?.(true);
		});
		expect(result.current.isConnected).toBe(true);
		unmount();
		expect(socket.stopStatus).toHaveBeenCalledOnce();
		expect(socket.stopMessages).toHaveBeenCalledOnce();
		expect(socket.unsubscribe).toHaveBeenCalledWith("platform_workers");
	});
});

describe("useWorkerWebSocket pool sequence", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		socket.connected = true;
		socket.poolListener = undefined;
	});

	it("preserves the REST active count through worker_online, then shows the heartbeat count", async () => {
		const { result } = renderHook(() => useWorkerWebSocket());
		await waitFor(() => expect(socket.poolListener).toBeDefined());
		const emit = socket.poolListener as (message: PoolMessage) => void;

		act(() => {
			emit({ type: "worker_online", worker_id: "worker-one" });
		});
		const placeholder = result.current.pools.find(
			(p) => p.worker_id === "worker-one",
		);
		expect(placeholder).toBeDefined();
		// No process data observed yet: the placeholder must not claim an
		// empty process list, or the merged row would display 0 active.
		expect(placeholder && "processes" in placeholder).toBe(false);
		expect(getPoolCounts(mergeWithRest(placeholder ?? {})).total).toBe(3);

		act(() => {
			emit({
				type: "worker_heartbeat",
				worker_id: "worker-one",
				processes: [],
			});
		});
		const afterHeartbeat = result.current.pools.find(
			(p) => p.worker_id === "worker-one",
		);
		expect(afterHeartbeat?.processes).toEqual([]);
		// A real (empty) heartbeat is observed data: it displays zero.
		expect(getPoolCounts(mergeWithRest(afterHeartbeat ?? {})).total).toBe(
			0,
		);
	});
});
