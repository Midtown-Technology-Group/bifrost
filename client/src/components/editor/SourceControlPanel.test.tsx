import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { DiffPreviewState } from "@/stores/editorStore";
import { SourceControlPanel } from "./SourceControlPanel";
import { GitOpError } from "./runGitOperation";

const mock = vi.hoisted(() => ({
	initialized: true, configured: true, missingStatus: false, statusError: false, statusLoading: false,
	results: {} as Record<string, unknown>, errors: {} as Record<string, Error>,
	fetch: vi.fn(), commit: vi.fn(), sync: vi.fn(), abort: vi.fn(), changes: vi.fn(), resolve: vi.fn(), diff: vi.fn(), discard: vi.fn(), cleanup: vi.fn(),
	refetch: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn(), append: vi.fn(), stream: vi.fn(),
	commits: { commits: [], total_commits: 0, has_more: false },
	editor: { sidebarPanel: "sourceControl", diffPreview: null as DiffPreviewState | null,
		setDiffPreview: (_value: DiffPreviewState | null | ((previous: DiffPreviewState | null) => DiffPreviewState | null)) => {} },
}));
vi.mock("sonner", () => ({ toast: { success: mock.success, error: mock.error, warning: mock.warning } }));
vi.mock("@/hooks/useGitHub", () => ({
	useGitStatus: () => ({ data: mock.missingStatus ? undefined : { initialized: mock.initialized, configured: mock.configured, current_branch: "main" },
		isLoading: mock.statusLoading, isError: mock.statusError, isFetching: false, refetch: mock.refetch }),
	useGitCommits: () => ({ data: mock.commits, isFetching: false, isError: false, refetch: mock.refetch }),
	useFetch: () => ({ mutateAsync: mock.fetch }), useCommit: () => ({ mutateAsync: mock.commit }),
	useSync: () => ({ mutateAsync: mock.sync }), useAbortMerge: () => ({ mutateAsync: mock.abort }),
	useWorkingTreeChanges: () => ({ mutateAsync: mock.changes }), useResolveConflicts: () => ({ mutateAsync: mock.resolve }),
	useFileDiff: () => ({ mutateAsync: mock.diff }), useDiscard: () => ({ mutateAsync: mock.discard }),
	useCleanupOrphaned: () => ({ mutateAsync: mock.cleanup }),
}));
vi.mock("@/stores/editorStore", () => {
	const store = Object.assign((selector: (value: typeof mock.editor) => unknown) => selector(mock.editor), {
		getState: () => ({ ...mock.editor, appendTerminalOutput: mock.append, streamTerminalLog: mock.stream }),
	});
	return { useEditorStore: store };
});
vi.mock("./runGitOperation", async importOriginal => {
	const original = await importOriginal<typeof import("./runGitOperation")>();
	return { ...original, runGitOp: vi.fn(async (queue: (id: string) => Promise<unknown>, kind: string,
		onQueued?: (id: string) => void, onUpdate?: (job: unknown) => void) => {
		await queue("fixture-job");
		onQueued?.("fixture-job");
		onUpdate?.({ status: "running", progress: { phase: "Observed durable job", percent: 50 } });
		if (mock.errors[kind]) throw mock.errors[kind];
		return mock.results[kind];
	}) };
});
const file = { path: "workflow.py", change_type: "modified", entity_type: "workflow", display_name: "Workflow" };
function panel() {
	return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><SourceControlPanel /></QueryClientProvider>);
}
async function ready() {
	panel();
	await waitFor(() => expect(screen.getByRole("button", { name: "Fetch from remote" })).toBeEnabled());
}
async function commit() {
	fireEvent.change(screen.getByLabelText("Commit message"), { target: { value: "  Reviewed update  " } });
	fireEvent.click(screen.getByRole("button", { name: "Commit changes" }));
	await waitFor(() => expect(mock.commit).toHaveBeenCalled());
}
beforeEach(() => {
	vi.clearAllMocks();
	mock.initialized = true; mock.configured = true; mock.missingStatus = false; mock.statusError = false; mock.statusLoading = false;
	mock.errors = {};
	mock.results = {
		status: { changed_files: [file], conflicts: [], commits_ahead: 0, commits_behind: 0 },
		fetch: { commits_ahead: 0, commits_behind: 0 }, commit: { success: true, files_committed: 1 },
		sync: { success: true, pushed_commits: 0, entities_imported: 0 },
		diff: { working_content: "local", head_content: "remote" },
	};
	for (const operation of [mock.fetch, mock.commit, mock.sync, mock.abort, mock.changes, mock.resolve, mock.diff, mock.discard]) {
		operation.mockReset().mockResolvedValue({ job_id: "fixture-job", status: "queued" });
	}
	mock.cleanup.mockReset().mockResolvedValue({ count: 1, cleaned: [{ entity_type: "workflow", entity_name: "Missing", path: "missing.py" }] });
	mock.editor.diffPreview = null;
	mock.editor.setDiffPreview = value => { mock.editor.diffPreview = typeof value === "function" ? value(mock.editor.diffPreview) : value; };
});
describe("source-control orchestration", () => {
	it("queues one commit with a trimmed message and logs observed progress", async () => {
		await ready(); await commit();
		await waitFor(() => expect(mock.success).toHaveBeenCalledWith("Committed 1 file(s)"));
		expect(mock.commit).toHaveBeenCalledExactlyOnceWith("Reviewed update", "fixture-job");
		expect(screen.getByLabelText("Commit message")).toHaveValue("");
		expect(mock.stream).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ message: "[50%] Observed durable job" }), "Running");
	});
	it("retains preflight warnings and all entity-change identities in terminal evidence", async () => {
		mock.results.commit = { success: true, files_committed: 3,
			preflight: { issues: [{ severity: "warning", category: "references", path: "a.py", message: "Review", fix_hint: "Inspect references" }] },
			entity_changes: [
				{ action: "added", entity_type: "workflow", name: "Added" },
				{ action: "updated", entity_type: "form", name: "Updated", reason: "Reviewed" },
				{ action: "removed", entity_type: "agent", name: "Removed" },
			] };
		await ready(); await commit();
		await waitFor(() => expect(mock.append).toHaveBeenCalledTimes(2));
		const evidence = JSON.stringify(mock.append.mock.calls);
		for (const name of ["Added", "Updated", "Removed", "references", "Reviewed"]) expect(evidence).toContain(name);
	});
	it("keeps rejected commit text and never silently retries", async () => {
		mock.errors.commit = new Error("permission denied");
		await ready(); await commit();
		await waitFor(() => expect(mock.error).toHaveBeenCalledWith("Commit failed: permission denied"));
		expect(mock.commit).toHaveBeenCalledTimes(1);
		expect(screen.getByLabelText("Commit message")).toHaveValue("  Reviewed update  ");
	});
	it("does not re-commit after an explicit orphan-cleanup failure", async () => {
		mock.errors.commit = new GitOpError("blocked", { preflight: { issues: [{ severity: "error", category: "orphan", path: "missing.py", line: 4,
			message: "Missing", fix_hint: "Review missing files", auto_fixable: true }] } });
		await ready(); await commit();
		await screen.findByRole("button", { name: "Clean up and retry" });
		mock.cleanup.mockRejectedValueOnce(new Error("cleanup refused"));
		fireEvent.click(screen.getByRole("button", { name: "Clean up and retry" }));
		await waitFor(() => expect(mock.error).toHaveBeenCalledWith("Cleanup failed: cleanup refused"));
		expect(mock.commit).toHaveBeenCalledTimes(1);
	});
	it("requires explicit deletion confirmation before sending confirm_deletes", async () => {
		mock.results.status = { changed_files: [], conflicts: [], commits_ahead: 0, commits_behind: 1 };
		mock.results.sync = { needs_delete_confirmation: true, pending_deletes: [{ action: "removed", entity_type: "workflow", name: "Reviewed deletion", path: "old.py" }] };
		await ready(); fireEvent.click(screen.getByRole("button", { name: "Sync origin" }));
		await screen.findByRole("button", { name: "Delete and sync" });
		expect(mock.sync).toHaveBeenCalledExactlyOnceWith("fixture-job", undefined);
		mock.results.sync = { success: true, pushed_commits: 1, entities_imported: 2, entity_changes: [{ action: "removed", entity_type: "workflow", name: "Reviewed deletion" }] };
		fireEvent.click(screen.getByRole("button", { name: "Delete and sync" }));
		await waitFor(() => expect(mock.sync).toHaveBeenCalledTimes(2));
		expect(mock.sync.mock.calls[1]).toEqual(["fixture-job", { confirm_deletes: true, retry_job_id: undefined }]);
		await waitFor(() => expect(mock.success).toHaveBeenCalledWith("Sync complete: pushed 1 commit(s), imported 2 entities, deleted 1 entity"));
	});
	it("reuses the observed job identity only for an explicit publication recovery", async () => {
		mock.results.status = { changed_files: [], conflicts: [], commits_ahead: 0, commits_behind: 1 };
		mock.results.sync = { success: false, retryable: true, error: "publication unavailable" };
		await ready(); fireEvent.click(screen.getByRole("button", { name: "Sync origin" }));
		const retry = await screen.findByRole("button", { name: /Retry publication/ });
		expect(mock.sync).toHaveBeenCalledTimes(1);
		mock.results.sync = { success: true, pushed_commits: 0, entities_imported: 0 };
		fireEvent.click(retry);
		await waitFor(() => expect(mock.sync).toHaveBeenCalledTimes(2));
		expect(mock.sync.mock.calls[1]).toEqual(["fixture-job", { confirm_deletes: false, retry_job_id: "fixture-job" }]);
	});
	it("uses the cached comparison without submitting another durable diff job", async () => {
		await ready(); fireEvent.click(screen.getByRole("button", { name: "View changes for workflow.py" }));
		await waitFor(() => expect(mock.editor.diffPreview?.localContent).toBe("local"));
		fireEvent.click(screen.getByRole("button", { name: "View changes for workflow.py" }));
		expect(mock.diff).toHaveBeenCalledExactlyOnceWith("workflow.py", "fixture-job");
		expect(mock.editor.diffPreview?.remoteContent).toBe("remote");
	});
	it("does not overwrite a newer selected comparison when an older job completes", async () => {
		let resolve!: (value: unknown) => void;
		mock.results.diff = new Promise(done => { resolve = done; });
		await ready(); fireEvent.click(screen.getByRole("button", { name: "View changes for workflow.py" }));
		await waitFor(() => expect(mock.diff).toHaveBeenCalled());
		mock.editor.diffPreview = { ...mock.editor.diffPreview!, path: "newer.py" };
		await act(async () => { resolve({ working_content: "old result", head_content: "old remote" }); });
		expect(mock.editor.diffPreview?.path).toBe("newer.py");
		expect(mock.editor.diffPreview?.localContent).not.toBe("old result");
	});
	it("reports fetch failure without presenting stale state as already up to date", async () => {
		mock.errors.fetch = new Error("transport unavailable");
		await ready(); fireEvent.click(screen.getByRole("button", { name: "Fetch from remote" }));
		await waitFor(() => expect(mock.error).toHaveBeenCalledWith("Fetch failed: transport unavailable"));
		expect(mock.fetch).toHaveBeenCalledTimes(1);
		expect(mock.success).not.toHaveBeenCalled();
	});
	it("refreshes working changes after a successful fetch", async () => {
		await ready(); fireEvent.click(screen.getByRole("button", { name: "Fetch from remote" }));
		await waitFor(() => expect(mock.success).toHaveBeenCalledWith("Already up to date"));
		expect(mock.changes).toHaveBeenCalledTimes(2);
	});
	it("keeps initialization and unavailable status distinct from an empty clean repository", () => {
		mock.initialized = false; panel();
		expect(mock.changes).not.toHaveBeenCalled();
		expect(screen.queryByLabelText("Commit message")).not.toBeInTheDocument();
	});
});
