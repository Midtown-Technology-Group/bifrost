import { act, render, screen } from "@testing-library/react";
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
    workflowManaged: false,
	showRole: false,
	workflowAccess: "authenticated",
	reset() {
		this.formsLoading = false;
        this.workflowManaged = false;
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
				role_ids: ["role-1"],
                is_solution_managed: sourceState.workflowManaged,
				solution_id: sourceState.workflowManaged ? "reviewed-solution" : null,
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


type Change = { accessLevel?: string; addRoleId?: string; clearRoles?: boolean };
type Boundary = {
    onOrganization: (ids: string[], scope: string | null) => Promise<void>;
    onAccess: (ids: string[], change: Change) => Promise<void>;
};
const boundary = vi.hoisted(() => ({ current: null as Boundary | null }));
vi.mock("@/components/entity-management/EntityAssignmentPanel", () => ({
    EntityAssignmentPanel: (props: Boundary) => {
        boundary.current = props;
        return <div>Boundary editor</div>;
    },
}));

async function mountBoundary() {
    boundary.current = null;
    apiPost.mockResolvedValue(relationshipAvailability());
    const user = userEvent.setup();
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><EntityManagement /></QueryClientProvider>);
    await user.click(await screen.findByRole("checkbox", { name: "Select Covi Portal" }));
    await user.click(screen.getByRole("button", { name: "Edit selected" }));
    await screen.findByText("Boundary editor");
    return user;
}
function mutations() {
    return [updateWorkflow, updateForm, updateAgent, updateApp, assignRole];
}

it.each(["organization", "access"])("refuses stale missing or managed IDs at the %s callback boundary", async mode => {
    sourceState.workflowManaged = true;
    await mountBoundary();
    for (const identity of ["workflow:missing", "workflow:workflow-1"]) {
        await act(async () => {
            const operation = mode === "organization"
                ? boundary.current!.onOrganization([identity], "org-1")
                : boundary.current!.onAccess([identity], { accessLevel: "everyone", addRoleId: "role-1" });
            await expect(operation).rejects.toThrow("Could not update:");
        });
    }
    for (const mutation of mutations()) expect(mutation).not.toHaveBeenCalled();
});

it.each(["admin", "", "public"])("rejects unsupported access %j before any assignment or resource write", async accessLevel => {
    await mountBoundary();
    await act(async () => {
        await expect(boundary.current!.onAccess(["app:app-1"], { accessLevel, addRoleId: "role-1" })).rejects.toThrow("Unsupported access level");
    });
    for (const mutation of mutations()) expect(mutation).not.toHaveBeenCalled();
});

it("an empty access change produces no writes for any eligible type", async () => {
    await mountBoundary();
    await act(async () => boundary.current!.onAccess(["workflow:workflow-1", "form:form-1", "agent:agent-1", "app:app-1"], {}));
    for (const mutation of mutations()) expect(mutation).not.toHaveBeenCalled();
});

it.each(["workflow", "form", "agent", "app"])("an explicit roles-only clear keeps %s scope unchanged", async kind => {
    await mountBoundary();
    await act(async () => boundary.current!.onAccess([`${kind}:${kind}-1`], { clearRoles: true }));
    const mutation = kind === "workflow" ? updateWorkflow : kind === "form" ? updateForm : kind === "agent" ? updateAgent : updateApp;
    expect(mutation).toHaveBeenCalledTimes(1);
    const payload = kind === "workflow" ? mutation.mock.calls[0][1] : mutation.mock.calls[0][0].body;
    expect(payload).toEqual({ access_level: "role_based", role_ids: [], ...(kind === "form" || kind === "agent" ? { clear_roles: false } : {}) });
    expect(payload).not.toHaveProperty("organization_id");
    expect(payload).not.toHaveProperty("scope");
    expect(assignRole).not.toHaveBeenCalled();
    for (const other of [updateWorkflow, updateForm, updateAgent, updateApp]) if (other !== mutation) expect(other).not.toHaveBeenCalled();
});

it("does not grant a role already present on the exact workflow", async () => {
    await mountBoundary();
    await act(async () => boundary.current!.onAccess(["workflow:workflow-1"], { addRoleId: "role-1" }));
    expect(assignRole).not.toHaveBeenCalled();
    expect(updateWorkflow).toHaveBeenCalledExactlyOnceWith("workflow-1", { access_level: "role_based" });
});

it("reports partial organization failure without replaying successful resources", async () => {
    updateWorkflow.mockRejectedValueOnce(new Error("write refused"));
    await mountBoundary();
    await act(async () => {
        await expect(boundary.current!.onOrganization(["workflow:workflow-1", "app:app-1"], "org-1")).rejects.toThrow("Other changes may have been applied");
    });
    expect(updateWorkflow).toHaveBeenCalledExactlyOnceWith("workflow-1", { organization_id: "org-1" });
    expect(updateApp).toHaveBeenCalledExactlyOnceWith({ params: { path: { app_id: "app-1" } }, body: { scope: "org-1" } });
    expect(updateForm).not.toHaveBeenCalled();
    expect(updateAgent).not.toHaveBeenCalled();
    expect(assignRole).not.toHaveBeenCalled();
    expect(screen.getByRole("checkbox", { name: "Select Covi Portal" })).toBeChecked();
});

it("does not perform the access write after a role assignment is refused", async () => {
    assignRole.mockRejectedValueOnce(new Error("role refused"));
    await mountBoundary();
    await act(async () => {
        await expect(boundary.current!.onAccess(["workflow:workflow-1"], { addRoleId: "new-reviewed-role" })).rejects.toThrow("Could not update:");
    });
    expect(assignRole).toHaveBeenCalledExactlyOnceWith("workflow", "workflow-1", "new-reviewed-role");
    for (const mutation of [updateWorkflow, updateForm, updateAgent, updateApp]) expect(mutation).not.toHaveBeenCalled();
});
