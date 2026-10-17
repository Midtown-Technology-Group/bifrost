// @vitest-environment happy-dom

import type { ReactNode } from "react";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentRunUpdate } from "@/services/websocket";

const ws = vi.hoisted(() => ({
	connect: vi.fn().mockResolvedValue(undefined),
	onAgentRunUpdate: vi.fn(),
	unsubscribe: vi.fn(),
}));

vi.mock("@/services/websocket", () => ({ webSocketService: ws }));

import { useAgentRunUpdates } from "./useAgentRunUpdates";

beforeEach(() => {
	vi.clearAllMocks();
	ws.onAgentRunUpdate.mockReturnValue(ws.unsubscribe);
});

describe("useAgentRunUpdates", () => {
	it("refreshes an accessible run detail and stats from its own channel", () => {
		const queryClient = new QueryClient();
		const invalidate = vi.spyOn(queryClient, "invalidateQueries");
		const onUpdate = vi.fn();
		const wrapper = ({ children }: { children: ReactNode }) => (
			<QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
		);
		const { unmount } = renderHook(
			() => useAgentRunUpdates({ agentId: "agent-1", runId: "run-1", onUpdate }),
			{ wrapper },
		);
		expect(ws.connect).toHaveBeenCalledWith(["agent-runs", "agent-run:run-1"]);
		const receive = ws.onAgentRunUpdate.mock.calls[0][0] as (update: AgentRunUpdate) => void;
		act(() => receive({ agent_id: "agent-2", run_id: "other-run" } as AgentRunUpdate));
		expect(invalidate).not.toHaveBeenCalled();
		act(() => receive({ agent_id: "agent-1", run_id: "run-1" } as AgentRunUpdate));
		expect(invalidate).toHaveBeenCalledWith({ queryKey: ["agent-runs"] });
		expect(invalidate).toHaveBeenCalledWith({ queryKey: ["get", "/api/agents/{agent_id}/stats"] });
		expect(invalidate).toHaveBeenCalledWith({
			queryKey: ["get", "/api/agent-runs/{run_id}", { params: { path: { run_id: "run-1" } } }],
		});
		expect(onUpdate).toHaveBeenCalledOnce();
		unmount();
		expect(ws.unsubscribe).toHaveBeenCalledOnce();
	});
});
