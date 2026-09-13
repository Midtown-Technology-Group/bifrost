import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SolutionActionsMenu } from "./SolutionActionsMenu";

const defaultProps = {
	exporting: false,
	isInactive: false,
	onCapture: vi.fn(),
	onExport: vi.fn(),
	onEdit: vi.fn(),
	onUninstall: vi.fn(),
	onHardDelete: vi.fn(),
};

describe("SolutionActionsMenu", () => {
	it("disables SDK updates for inactive solutions", async () => {
		const onUpdateAppSdks = vi.fn();
		render(<SolutionActionsMenu {...defaultProps} isInactive onUpdateAppSdks={onUpdateAppSdks} />);
		await userEvent.click(screen.getByTestId("solution-actions"));
		const action = screen.getByTestId("update-solution-app-sdks");
		expect(action).toHaveAttribute("data-disabled");
		await userEvent.click(action);
		expect(onUpdateAppSdks).not.toHaveBeenCalled();
	});
	it("labels the export action 'Export Solution'", async () => {
		render(<SolutionActionsMenu {...defaultProps} />);
		await userEvent.click(screen.getByTestId("solution-actions"));
		expect(
			screen.getByTestId("export-solution"),
		).toHaveTextContent("Export Solution");
	});

	it("shows Uninstall when active, hides it when inactive", async () => {
		const { rerender } = render(<SolutionActionsMenu {...defaultProps} />);
		await userEvent.click(screen.getByTestId("solution-actions"));
		expect(screen.getByTestId("uninstall-solution")).toBeInTheDocument();
		expect(screen.getByTestId("hard-delete-solution")).toBeInTheDocument();

		rerender(<SolutionActionsMenu {...defaultProps} isInactive />);
		expect(screen.queryByTestId("uninstall-solution")).not.toBeInTheDocument();
		expect(screen.getByTestId("hard-delete-solution")).toBeInTheDocument();
	});
});
