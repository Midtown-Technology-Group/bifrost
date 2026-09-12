import { beforeEach, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { WorkersTab } from "./WorkersTab";

const { usePools, useWorkerWebSocket } = vi.hoisted(() => ({
    usePools: vi.fn(),
    useWorkerWebSocket: vi.fn(),
}));
vi.mock("@/services/workers", () => ({
    usePools,
    useQueueStatus: () => ({ data: { items: [], total: 0 } }),
}));
vi.mock("../hooks/useWorkerWebSocket", () => ({ useWorkerWebSocket }));
vi.mock("./MemoryChart", () => ({
    MemoryChart: () => null,
    CONTAINER_COLORS: ["var(--primary)"],
}));
vi.mock("./QueueBadge", () => ({ QueueBadge: () => null }));

beforeEach(() => {
    usePools.mockReturnValue({ data: { pools: [{
        worker_id: "worker-one", status: "online", runtime: "aks",
        runtime_label: "Production AKS", active_process_count: 2,
        configured_capacity: 6, idle_count: 1, busy_count: 1,
    }] } });
    useWorkerWebSocket.mockReturnValue({ isConnected: true, pools: [{
        worker_id: "worker-one", status: "online", active_process_count: 3,
        idle_count: 2, busy_count: 1,
    }] });
});

it("retains runtime and capacity metadata when a partial heartbeat updates activity", () => {
    renderWithProviders(<WorkersTab />);
    expect(screen.getByText("Production AKS")).toBeInTheDocument();
    expect(screen.getByText(/3\/6 active forks/)).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Container worker-one" })).toHaveTextContent("3/6 active");
});

it("does not retain a stale runtime label when the heartbeat changes runtime", () => {
    useWorkerWebSocket.mockReturnValue({ isConnected: true, pools: [{
        worker_id: "worker-one", status: "online", runtime: "talos",
        active_process_count: 1, configured_capacity: 4,
    }] });
    renderWithProviders(<WorkersTab />);
    expect(screen.queryByText("Production AKS")).not.toBeInTheDocument();
    expect(screen.getByText("Talos pod")).toBeInTheDocument();
});
