import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { KnowledgeAssignDrawer } from "./KnowledgeAssignDrawer";

const state = vi.hoisted(() => ({
	failed: false,
	fetching: false,
	refetch: vi.fn(),
	success: vi.fn(),
	error: vi.fn(),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({
		data: [{ id: "org-a", name: "Home organization" }],
		isError: state.failed,
		isFetching: state.fetching,
		refetch: state.refetch,
	}),
}));
vi.mock("sonner", () => ({
	toast: { success: state.success, error: state.error },
}));

beforeEach(() => {
	vi.clearAllMocks();
	state.failed = false;
	state.fetching = false;
});

function setup(onAssign = vi.fn().mockResolvedValue(undefined)) {
	const onClose = vi.fn();
	const user = userEvent.setup();
	render(
		<KnowledgeAssignDrawer
			roleId="sharing-role"
			onAssign={onAssign}
			onClose={onClose}
		/>,
	);
	return { user, onAssign, onClose };
}

describe("KnowledgeAssignDrawer", () => {
	it("requires a namespace before assigning", async () => {
		const { user, onAssign } = setup();
		await user.click(
			screen.getByRole("button", { name: "Assign", exact: true }),
		);
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Namespace is required.",
		);
		expect(onAssign).not.toHaveBeenCalled();
	});

	it("submits a trimmed namespace with the selected organization", async () => {
		const { user, onAssign } = setup();
		await user.type(screen.getByLabelText("Namespace"), "  handbook  ");
		await user.selectOptions(
			screen.getByRole("combobox", { name: "Scope" }),
			"org-a",
		);
		await user.click(
			screen.getByRole("button", { name: "Assign", exact: true }),
		);
		await waitFor(() =>
			expect(onAssign).toHaveBeenCalledExactlyOnceWith([
				{ namespace: "handbook", organization_id: "org-a" },
			]),
		);
		expect(screen.getByLabelText("Namespace")).toHaveValue("");
		expect(state.success).toHaveBeenCalledWith(
			'Assigned namespace "handbook"',
		);
	});

	it("preserves null global scope and retains entries after a failed assignment", async () => {
		const onAssign = vi
			.fn()
			.mockRejectedValueOnce(new Error("Assignment refused"))
			.mockResolvedValueOnce(undefined);
		const { user } = setup(onAssign);
		await user.type(screen.getByLabelText("Namespace"), "shared-docs");
		await user.click(
			screen.getByRole("button", { name: "Assign", exact: true }),
		);
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Your entries are retained",
		);
		expect(screen.getByLabelText("Namespace")).toHaveValue("shared-docs");
		expect(screen.getByRole("combobox", { name: "Scope" })).toHaveValue(
			"global",
		);
		expect(onAssign).toHaveBeenCalledExactlyOnceWith([
			{ namespace: "shared-docs", organization_id: null },
		]);
		await user.click(
			screen.getByRole("button", { name: "Assign", exact: true }),
		);
		await waitFor(() =>
			expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
		);
		expect(onAssign).toHaveBeenCalledTimes(2);
	});

	it("blocks duplicate writes and dismissal until assignment finishes", async () => {
		let finish!: () => void;
		const pending = new Promise<void>((resolve) => {
			finish = resolve;
		});
		const { user, onAssign, onClose } = setup(
			vi.fn().mockReturnValue(pending),
		);
		await user.type(screen.getByLabelText("Namespace"), "shared-docs");
		await user.click(
			screen.getByRole("button", { name: "Assign", exact: true }),
		);
		expect(
			screen.getByRole("button", { name: "Assigning..." }),
		).toBeDisabled();
		const closeButtons = screen.getAllByRole("button", {
			name: "Close",
			exact: true,
		});
		expect(closeButtons[0]).toBeDisabled();
		await user.click(screen.getByRole("button", { name: "Assigning..." }));
		await user.keyboard("{Escape}");
		await user.click(closeButtons[1]);
		expect(onAssign).toHaveBeenCalledTimes(1);
		expect(onClose).not.toHaveBeenCalled();
		await act(async () => {
			finish();
		});
		await user.click(closeButtons[0]);
		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it("offers explicit recovery when organization choices fail to load", async () => {
		state.failed = true;
		const { user, onAssign } = setup();
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Organization choices could not load.",
		);
		await user.click(
			screen.getByRole("button", { name: "Retry organizations" }),
		);
		expect(state.refetch).toHaveBeenCalledTimes(1);
		expect(onAssign).not.toHaveBeenCalled();
	});
});
