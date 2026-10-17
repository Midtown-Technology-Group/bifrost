import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { KnowledgeTab } from "./KnowledgeTab";

const mock = vi.hoisted(() => ({
	data: undefined as { entries: Array<{ id: string; namespace: string; organization_id: string | null }> } | undefined,
	loading: false, failed: false, fetching: false, compact: false,
	remove: vi.fn(), assign: vi.fn(), refetch: vi.fn(), success: vi.fn(), error: vi.fn(),
}));
vi.mock("@/hooks/useRoles", () => ({
	useRoleKnowledge: () => ({ data: mock.data, isLoading: mock.loading, isError: mock.failed, isFetching: mock.fetching, refetch: mock.refetch }),
	useAssignKnowledgeToRole: () => ({ mutateAsync: mock.assign }),
	useBulkUnassignKnowledge: () => ({ mutateAsync: mock.remove }),
}));
vi.mock("@/hooks/useOrganizations", () => ({ useOrganizations: () => ({ data: [{ id: "org", name: "Reviewed Org" }] }) }));
vi.mock("@/hooks/useMediaQuery", () => ({ useMediaQuery: () => mock.compact }));
vi.mock("sonner", () => ({ toast: { success: mock.success, error: mock.error } }));
vi.mock("./KnowledgeAssignDrawer", () => ({ KnowledgeAssignDrawer: ({ onAssign, onClose }: { onAssign: (entries: Array<{ namespace: string; organization_id: null }>) => Promise<void>; onClose: () => void }) =>
	<div><button onClick={() => void onAssign([{ namespace: "reviewed", organization_id: null }])}>Confirm assignment</button><button onClick={onClose}>Close assignment</button></div>,
}));
beforeEach(() => {
	vi.clearAllMocks(); mock.data = { entries: [
		{ id: "global", namespace: "Shared", organization_id: null },
		{ id: "scoped", namespace: "Private", organization_id: "org" },
		{ id: "unknown", namespace: "Retained", organization_id: "unknown-org" },
	] }; mock.loading = false; mock.failed = false; mock.fetching = false; mock.compact = false;
	mock.remove.mockReset().mockResolvedValue({}); mock.assign.mockReset().mockResolvedValue({});
});
describe("knowledge assignment ownership and recovery", () => {
	it("shows exact Global, known and unresolved organization scopes", () => {
		render(<KnowledgeTab roleId="role" />);
		expect(screen.getByText("All organizations")).toBeInTheDocument();
		expect(screen.getByText("Reviewed Org")).toBeInTheDocument();
		expect(screen.getByText("unknown-org")).toBeInTheDocument();
	});
	it("never unassigns a selected namespace hidden by the current filter", async () => {
		render(<KnowledgeTab roleId="role" />);
		fireEvent.click(screen.getByLabelText("Select all visible namespaces"));
		fireEvent.change(screen.getByPlaceholderText("Search namespaces..."), { target: { value: "Reviewed Org" } });
		await waitFor(() => expect(screen.queryByText("Shared")).not.toBeInTheDocument());
		fireEvent.click(screen.getByRole("button", { name: "Unassign from role" }));
		await waitFor(() => expect(mock.remove).toHaveBeenCalledExactlyOnceWith({ params: { path: { role_id: "role" } }, body: { assignment_ids: ["scoped"] } }));
		await waitFor(() => expect(screen.queryByLabelText("Selected namespaces")).not.toBeInTheDocument());
	});
	it("supports explicit select, deselect, select-all and clear without effects", () => {
		render(<KnowledgeTab roleId="role" />);
		fireEvent.click(screen.getByLabelText("Select Shared"));
		fireEvent.click(screen.getByLabelText("Select Shared"));
		expect(screen.queryByLabelText("Selected namespaces")).not.toBeInTheDocument();
		fireEvent.click(screen.getByLabelText("Select all visible namespaces"));
		fireEvent.click(screen.getByLabelText("Select all visible namespaces"));
		expect(screen.queryByLabelText("Selected namespaces")).not.toBeInTheDocument();
		fireEvent.click(screen.getByLabelText("Select Shared"));
		fireEvent.click(screen.getByRole("button", { name: "Clear" }));
		expect(mock.remove).not.toHaveBeenCalled();
	});
	it("retains failed selection and requires an explicit retry", async () => {
		mock.remove.mockRejectedValueOnce(new Error("write refused"));
		render(<KnowledgeTab roleId="role" />); fireEvent.click(screen.getByLabelText("Select Private"));
		fireEvent.click(screen.getByRole("button", { name: "Unassign from role" }));
		await screen.findByText(/Your selection is retained/);
		expect(mock.remove).toHaveBeenCalledTimes(1); expect(screen.getByLabelText("Select Private")).toBeChecked();
		fireEvent.click(screen.getByRole("button", { name: "Unassign from role" }));
		await waitFor(() => expect(mock.success).toHaveBeenCalledWith("Removed 1 knowledge assignment(s)"));
	});
	it("disables repeated writes while the outcome is unknown", async () => {
		let finish!: () => void; mock.remove.mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve; }));
		render(<KnowledgeTab roleId="role" />); fireEvent.click(screen.getByLabelText("Select Shared"));
		fireEvent.click(screen.getByRole("button", { name: "Unassign from role" }));
		expect(screen.getByRole("button", { name: "Unassigning..." })).toBeDisabled();
		fireEvent.click(screen.getByRole("button", { name: "Unassigning..." })); expect(mock.remove).toHaveBeenCalledTimes(1);
		await act(async () => { finish(); });
	});
	it.each([true, false])("keeps failed reads distinct from empty results, cached=%s", cached => {
		mock.failed = true; if (!cached) mock.data = undefined;
		render(<KnowledgeTab roleId="role" />);
		expect(screen.getByRole("alert")).toHaveTextContent(cached ? /Previously loaded/ : /could not load/);
		fireEvent.click(screen.getByRole("button", { name: "Retry knowledge assignments" }));
		expect(mock.refetch).toHaveBeenCalledTimes(1); expect(mock.remove).not.toHaveBeenCalled();
	});
	it("distinguishes initial loading, empty results and no filter matches", async () => {
		mock.loading = true; const view = render(<KnowledgeTab roleId="role" />);
		expect(screen.queryByText("Shared")).not.toBeInTheDocument();
		mock.loading = false; mock.data = { entries: [] }; view.rerender(<KnowledgeTab roleId="role" />);
		expect(screen.getByText(/No knowledge namespaces assigned/)).toBeInTheDocument();
		fireEvent.change(screen.getByPlaceholderText("Search namespaces..."), { target: { value: "missing" } });
		await screen.findByText(/No assigned namespaces match/);
	});
	it("submits only the drawer's reviewed entries to the current role", async () => {
		render(<KnowledgeTab roleId="role" />); fireEvent.click(screen.getByRole("button", { name: "Assign namespace" }));
		fireEvent.click(screen.getByRole("button", { name: "Confirm assignment" }));
		await waitFor(() => expect(mock.assign).toHaveBeenCalledExactlyOnceWith({ params: { path: { role_id: "role" } }, body: { entries: [{ namespace: "reviewed", organization_id: null }] } }));
		fireEvent.click(screen.getByRole("button", { name: "Close assignment" }));
		expect(screen.queryByText("Confirm assignment")).not.toBeInTheDocument();
	});
	it("supports the compact card view with retained scope labels", () => {
		mock.compact = true; render(<KnowledgeTab roleId="role" />);
		expect(screen.getByText("Scope: Reviewed Org")).toBeInTheDocument();
	});
});
