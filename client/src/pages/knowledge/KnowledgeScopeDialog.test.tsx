import { beforeEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { KnowledgeScopeDialog } from "./KnowledgeScopeDialog";

const mock = vi.hoisted(() => ({ fetch: vi.fn(), saved: vi.fn(), close: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ authFetch: mock.fetch }));
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: ({ onChange, disabled }: { onChange: (scope: string | null | undefined) => void; disabled: boolean }) => <div>
		<button disabled={disabled} onClick={() => onChange(null)}>Global target</button>
		<button disabled={disabled} onClick={() => onChange("reviewed-org")}>Organization target</button>
		<button disabled={disabled} onClick={() => onChange(undefined)}>Unset target</button>
	</div>,
}));
beforeEach(() => {
	vi.clearAllMocks(); mock.fetch.mockReset().mockResolvedValue({ ok: true, status: 200, json: async () => ({ updated: 2 }) });
});
function mount(ids = ["first", "second"]) {
	return render(<KnowledgeScopeDialog documentIds={ids} onSaved={mock.saved} onClose={mock.close} />);
}
it.each(["global", "organization"])("sends only the selected documents to the explicit %s scope", async kind => {
	mount(); if (kind === "organization") fireEvent.click(screen.getByRole("button", { name: "Organization target" }));
	fireEvent.click(screen.getByRole("button", { name: "Update Scope" }));
	await waitFor(() => expect(mock.saved).toHaveBeenCalledExactlyOnceWith(2));
	expect(mock.fetch).toHaveBeenCalledTimes(1);
	expect(JSON.parse(mock.fetch.mock.calls[0][1].body)).toEqual({ document_ids: ["first", "second"], scope: kind === "global" ? "global" : "reviewed-org", replace: false });
	expect(mock.close).toHaveBeenCalledTimes(1);
});
it("does not replace matching documents until explicit conflict confirmation", async () => {
	mock.fetch.mockResolvedValueOnce({ ok: false, status: 409, json: async () => ({ detail: "Existing document keys" }) });
	mount(); fireEvent.click(screen.getByRole("button", { name: "Update Scope" }));
	await screen.findByText("Replace Existing Documents?");
	expect(mock.fetch).toHaveBeenCalledTimes(1); expect(mock.saved).not.toHaveBeenCalled();
	fireEvent.click(screen.getByRole("button", { name: "Replace" }));
	await waitFor(() => expect(mock.saved).toHaveBeenCalled());
	expect(JSON.parse(mock.fetch.mock.calls[1][1].body).replace).toBe(true);
});
it("allows returning from conflict without replacing or changing the target", async () => {
	mock.fetch.mockResolvedValueOnce({ ok: false, status: 409, json: async () => ({}) });
	mount(); fireEvent.click(screen.getByRole("button", { name: "Update Scope" }));
	await screen.findByText("Replace Existing Documents?"); fireEvent.click(screen.getByRole("button", { name: "Back" }));
	expect(screen.getByRole("button", { name: "Update Scope" })).toBeInTheDocument();
	expect(mock.fetch).toHaveBeenCalledTimes(1);
});
it.each(["transport", "server", "malformed-json"])("retains a failed %s outcome without retrying silently", async kind => {
	if (kind === "transport") mock.fetch.mockRejectedValueOnce(new Error("transport unavailable"));
	else mock.fetch.mockResolvedValueOnce({ ok: false, status: 503, json: kind === "malformed-json" ? async () => { throw new Error("malformed"); } : async () => ({ detail: { message: "server unavailable" } }) });
	mount(); fireEvent.click(screen.getByRole("button", { name: "Update Scope" }));
	await screen.findByRole("alert"); expect(mock.fetch).toHaveBeenCalledTimes(1); expect(mock.close).not.toHaveBeenCalled();
	fireEvent.click(screen.getByRole("button", { name: "Update Scope" }));
	await waitFor(() => expect(mock.saved).toHaveBeenCalled());
	expect(mock.fetch).toHaveBeenCalledTimes(2);
});
it("prevents closing or submitting another write while the outcome is unknown", async () => {
	let finish!: (value: unknown) => void; mock.fetch.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
	mount(); fireEvent.click(screen.getByRole("button", { name: "Update Scope" }));
	expect(screen.getByRole("button", { name: "Updating…" })).toBeDisabled();
	fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" }); expect(mock.close).not.toHaveBeenCalled();
	fireEvent.click(screen.getByRole("button", { name: "Updating…" })); expect(mock.fetch).toHaveBeenCalledTimes(1);
	await act(async () => { finish({ ok: true, status: 200, json: async () => ({ updated: 2 }) }); });
});
it("refuses an empty selection or unset target before a request", () => {
	const view = mount([]); fireEvent.click(screen.getByRole("button", { name: "Update Scope" })); expect(mock.fetch).not.toHaveBeenCalled();
	view.unmount(); mount(); fireEvent.click(screen.getByRole("button", { name: "Unset target" }));
	expect(screen.getByRole("button", { name: "Update Scope" })).toBeDisabled(); expect(mock.fetch).not.toHaveBeenCalled();
});
it("cancels without writing", () => {
	mount(); fireEvent.click(screen.getByRole("button", { name: "Cancel" })); expect(mock.close).toHaveBeenCalledTimes(1); expect(mock.fetch).not.toHaveBeenCalled();
});
