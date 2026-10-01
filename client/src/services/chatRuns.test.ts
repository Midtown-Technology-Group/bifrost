import { beforeEach, describe, expect, it, vi } from "vitest";
import {
	cancelChatRun,
	createChatRun,
	getChatRunState,
} from "./chatRuns";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("@/lib/api-client", () => ({
	apiClient: {
		GET: get,
		POST: post,
	},
}));

describe("chatRuns service", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("creates a durable run through the generated API contract", async () => {
		post.mockResolvedValue({
			data: { run_id: "run-1" },
			error: undefined,
			response: new Response(),
		});
		const body = {
			conversation_id: "conversation-1",
			content: "hello",
			client_run_id: "run-1",
			user_message_id: "message-1",
			attachment_ids: ["attachment-1"],
			model_profile_id: "profile-pro",
		};

		await createChatRun(body);

		expect(post).toHaveBeenCalledWith("/api/chat/runs", { body });
	});

	it("loads the durable conversation state", async () => {
		get.mockResolvedValue({
			data: { latest_sequence: 0 },
			error: undefined,
			response: new Response(),
		});

		await getChatRunState("conversation-1");

		expect(get).toHaveBeenCalledWith(
			"/api/chat/conversations/{conversation_id}/state",
			{ params: { path: { conversation_id: "conversation-1" } } },
		);
	});

	it("cancels a durable run", async () => {
		post.mockResolvedValue({
			data: { run_id: "run-1", status: "cancelled" },
			error: undefined,
			response: new Response(),
		});

		await cancelChatRun("run-1");

		expect(post).toHaveBeenCalledWith(
			"/api/chat/runs/{run_id}/cancel",
			{ params: { path: { run_id: "run-1" } } },
		);
	});
});
