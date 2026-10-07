import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, expect, it, vi } from "vitest";
import type { ComponentProps, ReactNode } from "react";

import { EntityManagement } from "./EntityManagement";

const authFetch = vi.hoisted(() => vi.fn());
const updateWorkflow = vi.hoisted(() => vi.fn());
const apiPost = vi.hoisted(() => vi.fn());
const updateForm = vi.hoisted(() => vi.fn());
const updateAgent = vi.hoisted(() => vi.fn());
const updateApp = vi.hoisted(() => vi.fn());
const assignRole = vi.hoisted(() => vi.fn());
const sourceState = vi.hoisted(() => ({
	formsLoading: false,
	showRole: false,
	workflowAccess: "authenticated",
	reset() {
		this.formsLoading = false;
		this.showRole = false;
		this.workflowAccess = "authenticated";
	},
}));
const mediaState = vi.hoisted(() => ({
	desktop: true,
	reset() {
		this.desktop = true;
	},
}));

vi.mock("framer-motion", () => {
	return {
		AnimatePresence: ({ children }: { children: ReactNode }) => (
			<>{children}</>
		),
		motion: {
			aside: ({
				children,
				initial: _initial,
				animate: _animate,
				exit: _exit,
				transition: _transition,
				...props
			}: ComponentProps<"aside"> & {
				initial?: unknown;
				animate?: unknown;
				exit?: unknown;
				transition?: unknown;
			}) => <aside {...props}>{children}</aside>,
		},
		useReducedMotion: () => false,
	};
});

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => authFetch(...args),
	apiClient: { POST: (...args: unknown[]) => apiPost(...args) },
}));

vi.mock("@/hooks/useMediaQuery", () => ({
	useIsDesktop: () => mediaState.desktop,
	useMediaQuery: () => mediaState.desktop,
}));

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
	},
}));

vi.mock("@/hooks/useWorkflows", () => ({
	useWorkflows: () => ({
		data: [
			{
				id: "workflow-1",
				name: "Create service request",
				organization_id: null,
				access_level: sourceState.workflowAccess,
				created_at: "2026-01-01T00:00:00Z",
				used_by_count: 1,
				is_solution_managed: false,
				solution_id: null,
			},
		],
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
	useUpdateWorkflow: () => ({ mutateAsync: updateWorkflow }),
}));

vi.mock("@/hooks/useForms", () => ({
	useForms: () => ({
		data: [
			{
				id: "form-1",
				name: "Service request intake",
				organization_id: null,
				access_level: "authenticated",
				created_at: "2026-01-01T00:00:00Z",
				dependency_count: 1,
				is_active: true,
				is_solution_managed: false,
				solution_id: null,
			},
		],
		isLoading: sourceState.formsLoading,
		isError: false,
		isFetching: sourceState.formsLoading,
		refetch: vi.fn(),
	}),
	useUpdateForm: () => ({ mutateAsync: updateForm }),
}));

vi.mock("@/hooks/useAgents", () => ({
	useAgents: () => ({
        data: [{ id: "agent-1", name: "Reviewed Agent", is_active: true, organization_id: null, access_level: "authenticated",
            created_at: "2026-01-01T00:00:00Z", role_ids: [], is_solution_managed: false, solution_id: null }],
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
	useUpdateAgent: () => ({ mutateAsync: updateAgent }),
}));

vi.mock("@/hooks/useApplications", () => ({
	useApplications: () => ({
		data: {
			applications: [
				{
					id: "app-1",
					name: "Covi Portal",
					slug: "covi-portal",
					organization_id: null,
					access_level: "authenticated",
					role_ids: [],
					created_at: "2026-01-01T00:00:00Z",
					is_solution_managed: false,
					solution_id: null,
				},
				{
					id: "app-2",
					name: "Unrelated Portal",
					slug: "unrelated-portal",
					organization_id: null,
					access_level: "authenticated",
					role_ids: [],
					created_at: "2026-01-01T00:00:00Z",
					is_solution_managed: false,
					solution_id: null,
				},
			],
		},
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
	useUpdateApplication: () => ({ mutateAsync: updateApp }),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({
        data: [{ id: "org-1", name: "Reviewed organization" }],
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
}));

vi.mock("@/hooks/useRoles", () => ({
	useRoles: () => ({
        data: sourceState.showRole ? [{ id: "role-1", name: "Reviewed role" }] : [],
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
}));

vi.mock("@/hooks/useDependencyGraph", () => ({
	useDependencyGraph: (entityType?: string, entityId?: string) => ({
		data:
			entityType === "app" && entityId === "app-1"
				? {
						root_id: "app:app-1",
						nodes: [
							{
								id: "app:app-1",
								type: "app",
								name: "Covi Portal",
								org_id: null,
							},
							{
								id: "workflow:workflow-1",
								type: "workflow",
								name: "Create service request",
								org_id: null,
							},
							{
								id: "form:form-1",
								type: "form",
								name: "Service request intake",
								org_id: null,
							},
						],
						edges: [
							{
								source: "app:app-1",
								target: "workflow:workflow-1",
								relationship: "uses",
							},
							{
								source: "form:form-1",
								target: "workflow:workflow-1",
								relationship: "uses",
							},
						],
					}
				: undefined,
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
}));

vi.mock("@/hooks/useAssignEntityRole", () => ({
	useAssignEntityRole: () => assignRole,
}));

beforeEach(() => {
	authFetch.mockReset();
	updateWorkflow.mockReset();
	updateWorkflow.mockResolvedValue({});
	apiPost.mockReset();
	for (const mutation of [updateForm, updateAgent, updateApp, assignRole]) mutation.mockReset().mockResolvedValue({});
    sourceState.reset();
	mediaState.reset();
});

function relationshipAvailability(overrides: Record<string, boolean> = {}) {
	return {
		data: {
			has_relationships: {
				"app:app-1": true,
				"app:app-2": false,
				"workflow:workflow-1": true,
				"form:form-1": true,
				"agent:agent-1": false,
				...overrides,
			},
		},
	};
}

vi.mock("@/components/forms/OrganizationSelect", () => ({
    OrganizationSelect: ({ value, onChange, disabled }: { value: string | null; onChange: (value: string | null) => void; disabled: boolean }) =>
        <select aria-label="Reviewed scope" value={value ?? "global"} disabled={disabled} onChange={event => onChange(event.target.value === "global" ? null : event.target.value)}>
            <option value="global">Global</option><option value="org-1">Reviewed organization</option>
        </select>,
}));

const kinds = [
    ["workflow", "Create service request", "workflow-1"],
    ["form", "Service request intake", "form-1"],
    ["agent", "Reviewed Agent", "agent-1"],
    ["app", "Covi Portal", "app-1"],
] as const;
function operation(kind: string) {
    if (kind === "workflow") return updateWorkflow;
    if (kind === "form") return updateForm;
    if (kind === "agent") return updateAgent;
    return updateApp;
}
async function openSelected(name: string) {
    const user = userEvent.setup();
    apiPost.mockResolvedValue(relationshipAvailability());
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><EntityManagement /></QueryClientProvider>);
    await user.click(await screen.findByRole("checkbox", { name: `Select ${name}` }));
    await user.click(screen.getByRole("button", { name: "Edit selected" }));
    return user;
}

it.each(kinds)("applies explicit scope only to the selected %s without clearing roles", async (kind, name, id) => {
    const user = await openSelected(name);
    await user.click(screen.getByRole("combobox", { name: "Organization change mode" }));
    await user.click(screen.getByRole("option", { name: "Set scope" }));
    fireEvent.change(screen.getByLabelText("Reviewed scope"), { target: { value: "org-1" } });
    await user.click(screen.getByRole("button", { name: "Apply changes" }));
    await waitFor(() => expect(operation(kind)).toHaveBeenCalledTimes(1));
    if (kind === "workflow") expect(updateWorkflow).toHaveBeenCalledExactlyOnceWith(id, { organization_id: "org-1" });
    else if (kind === "app") expect(updateApp).toHaveBeenCalledExactlyOnceWith({ params: { path: { app_id: id } }, body: { scope: "org-1" } });
    else expect(operation(kind)).toHaveBeenCalledExactlyOnceWith({ params: { path: { [`${kind}_id`]: id } }, body: { organization_id: "org-1", clear_roles: false } });
    for (const other of ["workflow", "form", "agent", "app"]) if (other !== kind) expect(operation(other)).not.toHaveBeenCalled();
    expect(assignRole).not.toHaveBeenCalled();
});

it.each(kinds)("retains the explicit access level while clearing roles on the selected %s", async (kind, name, id) => {
    const user = await openSelected(name);
    await user.click(screen.getByRole("combobox", { name: "Access level change" }));
    await user.click(screen.getByRole("option", { name: /^Everyone Any signed-in/ }));
    await user.click(screen.getByRole("combobox", { name: "Roles change" }));
    await user.click(screen.getByRole("option", { name: "Clear roles" }));
    await user.click(screen.getByRole("button", { name: "Apply changes" }));
    await waitFor(() => expect(operation(kind)).toHaveBeenCalledTimes(1));
    if (kind === "workflow") expect(updateWorkflow).toHaveBeenCalledExactlyOnceWith(id, { access_level: "everyone", role_ids: [] });
    else expect(operation(kind)).toHaveBeenCalledExactlyOnceWith({ params: { path: { [`${kind}_id`]: id } }, body: {
        access_level: "everyone", role_ids: [], ...(kind === "app" ? {} : { clear_roles: false }),
    } });
    expect(assignRole).not.toHaveBeenCalled();
});

it.each(kinds)("adds one reviewed role to the selected %s without changing its organization", async (kind, name, id) => {
    sourceState.showRole = true;
    const user = await openSelected(name);
    await user.click(screen.getByRole("combobox", { name: "Roles change" }));
    await user.click(screen.getByRole("option", { name: "Add role" }));
    await user.click(screen.getByRole("combobox", { name: "Role to add" }));
    await user.click(screen.getByRole("option", { name: "Reviewed role" }));
    await user.click(screen.getByRole("button", { name: "Apply changes" }));
    await waitFor(() => expect(assignRole).toHaveBeenCalledExactlyOnceWith(kind, id, "role-1"));
    await waitFor(() => expect(operation(kind)).toHaveBeenCalledTimes(1));
    const call = operation(kind).mock.calls[0];
    const body = kind === "workflow" ? call[1] : call[0].body;
    expect(body.access_level).toBe("role_based");
    expect(body).not.toHaveProperty("organization_id");
    expect(body).not.toHaveProperty("scope");
});

it("retains failed access edits and does not silently replay the requested write", async () => {
    updateWorkflow.mockRejectedValueOnce(new Error("update refused"));
    const user = await openSelected("Create service request");
    await user.click(screen.getByRole("combobox", { name: "Access level change" }));
    await user.click(screen.getByRole("option", { name: /^Everyone Any signed-in/ }));
    await user.click(screen.getByRole("button", { name: "Apply changes" }));
    await screen.findByText(/Could not update: Create service request/);
    expect(updateWorkflow).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("checkbox", { name: "Select Create service request" })).toBeChecked();
    expect(screen.getByRole("combobox", { name: "Access level change" })).toHaveTextContent("Everyone");
});
