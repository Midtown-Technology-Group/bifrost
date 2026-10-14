import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MCPConnectionEdit } from "./MCPConnectionEdit";

const mock = vi.hoisted(() => ({
	server: undefined as Record<string, unknown> | undefined,
	connection: undefined as Record<string, unknown> | undefined,
	loading: false, failed: false, fetching: false,
	update: vi.fn(), refresh: vi.fn(), remove: vi.fn(), patchTool: vi.fn(),
	reset: vi.fn(), refetch: vi.fn(), success: vi.fn(), error: vi.fn(),
}));
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => ({ user: { email: "operator@example.test" } }) }));
vi.mock("@/hooks/useOrganizations", () => ({ useOrganizations: () => ({ data: [{ id: "org", name: "Reviewed organization" }] }) }));
vi.mock("sonner", () => ({ toast: { success: mock.success, error: mock.error } }));
vi.mock("@/lib/api-client", () => ({
	$api: {
		useQuery: (_method: string, path: string) => ({
			data: path === "/api/mcp-servers/{server_id}" ? mock.server : mock.connection,
			isLoading: mock.loading, error: mock.failed ? new Error("read refused") : null,
			isFetching: mock.fetching, refetch: mock.refetch,
		}),
		useMutation: (method: string, path: string) => {
			const [isError, setError] = useState(false);
			const operation = method === "delete" ? mock.remove : path.endsWith("refresh-tools") ? mock.refresh : mock.update;
			return {
				mutateAsync: async (input: unknown) => {
					setError(false);
					try { return await operation(input); }
					catch (error) { setError(true); throw error; }
				},
				isPending: false, isError, reset: mock.reset,
			};
		},
	},
	apiClient: { PATCH: mock.patchTool, POST: vi.fn(), GET: vi.fn() },
}));
const connection = {
	id: "connection", organization_id: "org", client_id: "original-client",
	server_url_override: null, available_in_chat: false, available_to_autonomous: false,
	service_oauth_token_id: "retained-token-identity",
	tools: [{ id: "tool", tool_name: "reviewed_read", enabled: true, disabled_reason: null }],
};
function mount() {
	return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
		<MemoryRouter initialEntries={["/mcp-servers/server/connections/connection"]}><Routes>
			<Route path="/mcp-servers/:serverId/connections/:connectionId" element={<MCPConnectionEdit />} />
			<Route path="/mcp-servers/server" element={<p>Server details</p>} />
			<Route path="/mcp-servers" element={<p>Server list</p>} />
		</Routes></MemoryRouter>
	</QueryClientProvider>);
}
async function ready() {
	mount();
	await waitFor(() => expect(screen.getByLabelText("Client ID")).toHaveValue("original-client"));
}
beforeEach(() => {
	vi.clearAllMocks();
	mock.server = { id: "server", name: "Reviewed server", server_url: "https://vendor.example.test/mcp", oauth_flow_type: "authorization_code" };
	mock.connection = { ...connection };
	mock.loading = false; mock.failed = false; mock.fetching = false;
	mock.update.mockReset().mockResolvedValue({});
	mock.refresh.mockReset().mockResolvedValue({ enabled: 1, total: 1 });
	mock.remove.mockReset().mockResolvedValue({});
	mock.patchTool.mockReset().mockResolvedValue({ data: {} });
});
describe("connection edits and recovery", () => {
	it("preserves existing secrets and sends only reviewed connection fields", async () => {
		await ready();
		fireEvent.change(screen.getByLabelText("Client ID"), { target: { value: "reviewed-client" } });
		fireEvent.click(screen.getByRole("switch", { name: "Available in user chat" }));
		fireEvent.click(screen.getByRole("button", { name: "Save" }));
		await waitFor(() => expect(mock.success).toHaveBeenCalledWith("Connection saved"));
		expect(mock.update).toHaveBeenCalledExactlyOnceWith({ params: { path: { connection_id: "connection" } }, body: {
			client_id: "reviewed-client", server_url_override: null, available_in_chat: true, available_to_autonomous: false,
		} });
		expect(mock.patchTool).not.toHaveBeenCalled();
	});
	it("replaces a secret only with an explicit selection and clears it after success", async () => {
		await ready();
		fireEvent.click(screen.getByRole("switch", { name: "Set new client secret" }));
		fireEvent.change(screen.getByLabelText("New client secret"), { target: { value: "synthetic-test-value" } });
		expect(screen.getByLabelText("New client secret")).toHaveAttribute("type", "password");
		fireEvent.click(screen.getByRole("button", { name: "Show client secret" }));
		expect(screen.getByLabelText("New client secret")).toHaveAttribute("type", "text");
		fireEvent.click(screen.getByRole("button", { name: "Hide client secret" }));
		fireEvent.click(screen.getByRole("button", { name: "Save" }));
		await waitFor(() => expect(mock.success).toHaveBeenCalledWith("Connection saved"));
		expect(mock.update.mock.calls[0][0].body.client_secret).toBe("synthetic-test-value");
		expect(screen.queryByLabelText("New client secret")).not.toBeInTheDocument();
	});
	it("does not overwrite a secret when the replacement field is empty", async () => {
		await ready(); fireEvent.click(screen.getByRole("switch", { name: "Set new client secret" }));
		fireEvent.click(screen.getByRole("button", { name: "Save" }));
		await waitFor(() => expect(mock.success).toHaveBeenCalled());
		expect(mock.update.mock.calls[0][0].body).not.toHaveProperty("client_secret");
	});
	it("preserves rejected edits and requires an explicit retry", async () => {
		mock.update.mockRejectedValueOnce(new Error("save refused"));
		await ready(); fireEvent.change(screen.getByLabelText("Client ID"), { target: { value: "retained-edit" } });
		fireEvent.click(screen.getByRole("button", { name: "Save" }));
		await screen.findByText("save refused");
		expect(screen.getByLabelText("Client ID")).toHaveValue("retained-edit");
		expect(mock.update).toHaveBeenCalledTimes(1);
		expect(mock.success).not.toHaveBeenCalled();
		fireEvent.click(screen.getByRole("button", { name: "Retry save" }));
		await waitFor(() => expect(mock.success).toHaveBeenCalledWith("Connection saved"));
		expect(mock.update).toHaveBeenCalledTimes(2);
	});
	it("sends only changed tool choices and retains failed choices for recovery", async () => {
		mock.patchTool.mockResolvedValueOnce({ error: { message: "refused" } });
		await ready(); fireEvent.click(screen.getByRole("checkbox", { name: "reviewed_read" }));
		fireEvent.click(screen.getByRole("button", { name: "Save" }));
		await screen.findByText(/Connection saved, but 1 tool change could not be saved/);
		expect(mock.patchTool).toHaveBeenCalledExactlyOnceWith("/api/mcp-connections/{connection_id}/tools/{tool_id}", {
			params: { path: { connection_id: "connection", tool_id: "tool" } }, body: { enabled: false },
		});
		expect(screen.getByRole("checkbox", { name: "reviewed_read" })).not.toBeChecked();
		expect(mock.success).not.toHaveBeenCalled();
		fireEvent.click(screen.getByRole("button", { name: "Retry save" }));
		await waitFor(() => expect(mock.success).toHaveBeenCalledWith("Connection saved (1 tool toggle(s) applied)"));
	});
	it("does not patch tools after connection-level rejection", async () => {
		mock.update.mockRejectedValueOnce(new Error("save refused"));
		await ready(); fireEvent.click(screen.getByRole("checkbox", { name: "reviewed_read" }));
		fireEvent.click(screen.getByRole("button", { name: "Save" }));
		await screen.findByText("save refused"); expect(mock.patchTool).not.toHaveBeenCalled();
	});
	it("rejects a second save while the first outcome is unknown", async () => {
		let finish!: () => void;
		mock.update.mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve; }));
		await ready(); fireEvent.click(screen.getByRole("button", { name: "Save" }));
		expect(screen.getByLabelText("Client ID")).toBeDisabled();
		fireEvent.click(screen.getByRole("button", { name: /Saving/ }));
		expect(mock.update).toHaveBeenCalledTimes(1);
		await act(async () => { finish(); });
		await waitFor(() => expect(mock.success).toHaveBeenCalledWith("Connection saved"));
	});
	it("keeps catalog failure distinct from a completed refresh", async () => {
		mock.refresh.mockRejectedValueOnce(new Error("catalog unavailable"));
		await ready(); fireEvent.click(screen.getByRole("button", { name: "Refresh catalog" }));
		await screen.findByText("catalog unavailable");
		expect(mock.refresh).toHaveBeenCalledTimes(1); expect(mock.success).not.toHaveBeenCalled();
		fireEvent.click(screen.getByRole("button", { name: "Retry catalog refresh" }));
		await waitFor(() => expect(mock.success).toHaveBeenCalledWith("Catalog refreshed — 1 enabled / 1 total"));
	});
	it("disconnects only the selected connection and retains an explicit retry on failure", async () => {
		mock.update.mockRejectedValueOnce(new Error("disconnect refused"));
		await ready(); fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
		await screen.findByText("Could not disconnect the service. Try again.");
		expect(mock.update).toHaveBeenCalledExactlyOnceWith({ params: { path: { connection_id: "connection" } }, body: { service_oauth_token_id: null } });
		fireEvent.click(screen.getByRole("button", { name: "Retry disconnect" }));
		await waitFor(() => expect(mock.success).toHaveBeenCalledWith("Service connection cleared"));
	});
	it("does not refresh a disconnected catalog", async () => {
		mock.connection = { ...connection, service_oauth_token_id: null, tools: [] };
		await ready(); expect(screen.getByRole("button", { name: "Refresh catalog" })).toBeDisabled();
		expect(screen.getByText(/No tools cached/)).toBeInTheDocument(); expect(mock.refresh).not.toHaveBeenCalled();
	});
	it("requires deletion confirmation and retains failed outcomes without silent replay", async () => {
		mock.remove.mockRejectedValueOnce(new Error("delete refused"));
		await ready(); fireEvent.click(screen.getByRole("button", { name: "Delete connection" }));
		expect(mock.remove).not.toHaveBeenCalled();
		fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete connection" }));
		await waitFor(() => expect(mock.error).toHaveBeenCalledWith("delete refused"));
		expect(mock.remove).toHaveBeenCalledTimes(1); expect(screen.getByRole("alertdialog")).toBeInTheDocument();
		fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Retry deletion" }));
		await screen.findByText("Server details");
		expect(mock.remove.mock.calls[1]).toEqual([{ params: { path: { connection_id: "connection" } } }]);
	});
	it("distinguishes a failed read from a missing connection and never enables writes", () => {
		mock.connection = undefined; mock.failed = true; mount();
		expect(screen.getByText("Connection unavailable")).toBeInTheDocument();
		fireEvent.click(screen.getByRole("button", { name: "Retry connection" }));
		expect(mock.refetch).toHaveBeenCalledTimes(2); expect(mock.update).not.toHaveBeenCalled();
	});
	it("renders initial loading without inventing a connection", () => {
		mock.connection = undefined; mock.loading = true; mount();
		expect(screen.getByRole("status", { name: "Loading connection" })).toBeInTheDocument();
		expect(mock.update).not.toHaveBeenCalled();
	});
	it("returns to the list when the selected connection is absent", async () => {
		mock.connection = undefined; mount();
		expect(screen.getByText("Connection not found.")).toBeInTheDocument();
		fireEvent.click(screen.getByRole("button", { name: "Back to MCP Servers" }));
		await screen.findByText("Server list"); expect(mock.update).not.toHaveBeenCalled();
	});
});
