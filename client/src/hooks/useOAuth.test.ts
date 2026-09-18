import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "@/lib/api-client";
import { handleOAuthCallback } from "./useOAuth";

vi.mock("@/lib/api-client", () => ({
	$api: {},
	apiClient: { POST: vi.fn() },
}));

describe("handleOAuthCallback", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		vi.mocked(apiClient.POST).mockResolvedValue({
			data: {},
			error: undefined,
		} as never);
	});

	it("forwards callback_url_params so entity_id can be captured", async () => {
		await handleOAuthCallback(
			"integration-1",
			"code-1",
			"state-1",
			"https://app.example/oauth/callback/integration-1",
			{ realmId: "9130350000000000" },
		);

		expect(apiClient.POST).toHaveBeenCalledWith(
			"/api/oauth/callback/{connection_name}",
			{
				params: { path: { connection_name: "integration-1" } },
				body: {
					code: "code-1",
					state: "state-1",
					redirect_uri:
						"https://app.example/oauth/callback/integration-1",
					callback_url_params: { realmId: "9130350000000000" },
				},
			},
		);
	});

	it("sends null params when none are supplied", async () => {
		await handleOAuthCallback("integration-1", "code-1");

		expect(apiClient.POST).toHaveBeenCalledWith(
			"/api/oauth/callback/{connection_name}",
			{
				params: { path: { connection_name: "integration-1" } },
				body: {
					code: "code-1",
					state: null,
					redirect_uri: null,
					callback_url_params: null,
				},
			},
		);
	});
});
