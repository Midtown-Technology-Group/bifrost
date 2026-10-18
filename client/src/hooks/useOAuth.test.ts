import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { handleOAuthCallback, useRefreshOAuthToken } from "./useOAuth";

const { post, useMutation, invalidate, toastSuccess, toastError } = vi.hoisted(() => ({
	post: vi.fn(),
	useMutation: vi.fn(),
	invalidate: vi.fn(),
	toastSuccess: vi.fn(),
	toastError: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
	$api: { useMutation },
	apiClient: { POST: post },
}));

vi.mock("@tanstack/react-query", () => ({ useQueryClient: () => ({ invalidateQueries: invalidate }) }));
vi.mock("sonner", () => ({ toast: { success: toastSuccess, error: toastError } }));

describe("handleOAuthCallback", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		post.mockResolvedValue({
			data: {},
			error: undefined,
		});
	});

	it("forwards callback_url_params so entity_id can be captured", async () => {
		await handleOAuthCallback(
			"integration-1",
			"code-1",
			"state-1",
			"https://app.example/oauth/callback/integration-1",
			{ realmId: "9130350000000000" },
		);

		expect(post).toHaveBeenCalledWith(
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

		expect(post).toHaveBeenCalledWith(
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

type RefreshOptions = {
	onSuccess: (data: { success: boolean; message: string }, variables: unknown) => void;
};

function refreshOptions(): RefreshOptions {
	renderHook(() => useRefreshOAuthToken());
	return useMutation.mock.calls[0][2] as RefreshOptions;
}

const variables = { params: { path: { connection_name: "printix" } } };

beforeEach(() => vi.clearAllMocks());

it("reports a 200 response with success=false as a refresh failure", () => {
	refreshOptions().onSuccess(
		{ success: false, message: "Token refresh failed: certificate verify failed" },
		variables,
	);
	expect(toastError).toHaveBeenCalledWith("Token refresh failed: certificate verify failed");
	expect(toastSuccess).not.toHaveBeenCalled();
	expect(invalidate).toHaveBeenCalledTimes(2);
});

it("reports success only when the response says the refresh succeeded", () => {
	refreshOptions().onSuccess({ success: true, message: "Token refreshed" }, variables);
	expect(toastSuccess).toHaveBeenCalledWith("OAuth token refreshed successfully");
	expect(toastError).not.toHaveBeenCalled();
	expect(invalidate).toHaveBeenCalledTimes(2);
});
