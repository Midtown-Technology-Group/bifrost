import { beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ConversationUsage, OrganizationUsage, UsageReportResponse, WorkflowUsage } from "@/services/usage";
import { AgentTable, ConversationTable, KnowledgeStorageTable, OrganizationTable, WorkflowTable } from "./UsageTables";
import { downloadReportCSV } from "./report-csv";

vi.mock("./report-csv", () => ({ downloadReportCSV: vi.fn() }));
const period = { startDate: "2026-10-01", endDate: "2026-10-07", isDemo: false, isLoading: false };
const workflows: WorkflowUsage[] = [
	{ workflow_name: "Alpha", execution_count: 1, input_tokens: 10, output_tokens: 2, ai_cost: "1.00", cpu_seconds: 1, memory_bytes: 1048576 },
	{ workflow_name: "Beta", execution_count: 2, input_tokens: 20, output_tokens: 4, ai_cost: "2.00", cpu_seconds: 2, memory_bytes: 2097152 },
];
const conversations: ConversationUsage[] = [
	{ conversation_id: "a", conversation_title: null, message_count: 1, input_tokens: 10, output_tokens: 2 },
	{ conversation_id: "b", conversation_title: "Zeta", message_count: 2, input_tokens: 20, output_tokens: 4, ai_cost: "2.00" },
];
const organizations: OrganizationUsage[] = [
	{ organization_id: "a", organization_name: "Alpha", execution_count: 1, conversation_count: 1, input_tokens: 10, output_tokens: 2 },
	{ organization_id: "b", organization_name: "Beta", execution_count: 2, conversation_count: 2, input_tokens: 20, output_tokens: 4, ai_cost: "2.00" },
];
const storage: UsageReportResponse = {
	summary: { total_input_tokens: 0, total_output_tokens: 0, total_ai_calls: 0, total_cpu_seconds: 0, peak_memory_bytes: 0 },
	knowledge_storage_as_of: "2026-10-06",
	knowledge_storage: [
		{ organization_id: "a", organization_name: "Alpha", namespace: "alpha", document_count: 1, size_bytes: 1048576, size_mb: 1 },
		{ organization_id: "b", organization_name: "Beta", namespace: "beta", document_count: 2, size_bytes: 2097152, size_mb: 2 },
	],
};
function firstRecord() {
	const rows = within(screen.getByRole("table")).getAllByRole("row");
	return within(rows[1]).getAllByRole("cell")[0].textContent;
}
function verifySort(columns: string[], descending: string, ascending: string) {
	for (const column of columns) {
		const button = within(screen.getByRole("columnheader", { name: column })).getByRole("button");
		fireEvent.click(button);
		expect(firstRecord()).toBe(descending);
		fireEvent.click(button);
		expect(firstRecord()).toBe(ascending);
	}
}

beforeEach(() => vi.clearAllMocks());
describe("usage table records", () => {
	it("sorts all workflow metrics without mutating source records and exports exact resource units", () => {
		render(<WorkflowTable {...period} workflows={workflows} />);
		expect(firstRecord()).toBe("Beta");
		verifySort(["Workflow", "Executions", "Tokens", "AI Cost", "CPU", "Memory"], "Beta", "Alpha");
		expect(workflows.map(row => row.workflow_name)).toEqual(["Alpha", "Beta"]);
		fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
		expect(downloadReportCSV).toHaveBeenCalledExactlyOnceWith(
			"usage-by-workflow-2026-10-01-2026-10-07.csv",
			["Workflow Name", "Executions", "Input Tokens", "Output Tokens", "AI Cost", "CPU Seconds", "Memory (MB)"],
			[["Alpha", 1, 10, 2, "1.00", 1, "1.00"], ["Beta", 2, 20, 4, "2.00", 2, "2.00"]],
		);
	});
	it("keeps untitled conversations and zero cost in both sorted records and demo exports", () => {
		render(<ConversationTable {...period} isDemo conversations={conversations} />);
		verifySort(["Conversation", "Messages", "Tokens", "AI Cost"], "Zeta", "Untitled");
		fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
		expect(downloadReportCSV).toHaveBeenCalledWith(
			"usage-by-conversation-2026-10-01-2026-10-07-demo.csv",
			["Conversation Title", "Message Count", "Input Tokens", "Output Tokens", "AI Cost"],
			[["Untitled", 1, 10, 2, "0"], ["Zeta", 2, 20, 4, "2.00"]],
		);
	});
	it("sorts organization totals and exports missing cost as zero", () => {
		render(<OrganizationTable {...period} organizations={organizations} />);
		verifySort(["Organization", "Executions", "Conversations", "Tokens", "AI Cost"], "Beta", "Alpha");
		fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
		expect(downloadReportCSV).toHaveBeenCalledWith(
			"usage-by-organization-2026-10-01-2026-10-07.csv",
			["Organization", "Executions", "Conversations", "Input Tokens", "Output Tokens", "AI Cost"],
			[["Alpha", 1, 1, 10, 2, "0"], ["Beta", 2, 2, 20, 4, "2.00"]],
		);
	});
	it("uses the storage snapshot date and preserves exact bytes alongside display megabytes", () => {
		render(<KnowledgeStorageTable {...period} data={storage} isDemo />);
		verifySort(["Organization", "Namespace", "Documents", "Size"], "Beta", "Alpha");
		fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
		expect(downloadReportCSV).toHaveBeenCalledWith(
			"knowledge-storage-2026-10-06-demo.csv",
			["Organization", "Namespace", "Documents", "Size (MB)", "Size (Bytes)"],
			[["Alpha", "alpha", 1, "1.00", 1048576], ["Beta", "beta", 2, "2.00", 2097152]],
		);
	});
	it("uses the requested date when storage has no snapshot date", () => {
		render(<KnowledgeStorageTable {...period} data={{ ...storage, knowledge_storage_as_of: null }} />);
		fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
		expect(vi.mocked(downloadReportCSV).mock.calls[0][0]).toBe("knowledge-storage-2026-10-01.csv");
	});
	it("sorts agent usage without losing output-token records", () => {
		render(<AgentTable isLoading={false} agents={[
			{ agent_name: "Alpha", run_count: 1, input_tokens: 10, output_tokens: 2 },
			{ agent_name: "Beta", run_count: 2, input_tokens: 20, output_tokens: 4, ai_cost: "2.00" },
		]} />);
		verifySort(["Agent", "Runs", "Input Tokens", "AI Cost"], "Beta", "Alpha");
		expect(within(screen.getByRole("table")).getByRole("columnheader", { name: "Output Tokens" })).toBeInTheDocument();
	});
	it.each(["loading", "absent", "empty"])("does not export or fabricate records for %s data", state => {
		const loading = state === "loading";
		const renderers = [
			() => <WorkflowTable {...period} isLoading={loading} workflows={state === "empty" ? [] : undefined} />,
			() => <ConversationTable {...period} isLoading={loading} conversations={state === "empty" ? [] : undefined} />,
			() => <OrganizationTable {...period} isLoading={loading} organizations={state === "empty" ? [] : undefined} />,
			() => <KnowledgeStorageTable {...period} isLoading={loading} data={state === "empty" ? { ...storage, knowledge_storage: [] } : undefined} />,
		];
		for (const component of renderers) {
			render(component());
			expect(screen.queryByRole("table")).not.toBeInTheDocument();
			expect(screen.getByRole("button", { name: "Export CSV" })).toBeDisabled();
			cleanup();
		}
		render(<AgentTable isLoading={loading} agents={state === "empty" ? [] : undefined} />);
		expect(screen.queryByRole("table")).not.toBeInTheDocument();
		expect(downloadReportCSV).not.toHaveBeenCalled();
	});
});
