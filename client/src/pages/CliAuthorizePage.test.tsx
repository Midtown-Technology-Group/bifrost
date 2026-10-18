import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockNavigate = vi.fn();
const authState = {
	isAuthenticated: true,
	isLoading: false,
	user: { email: "user@example.test" },
};

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => authState,
}));

vi.mock("react-router", async () => {
	const actual =
		await vi.importActual<typeof import("react-router")>("react-router");
	return { ...actual, useNavigate: () => mockNavigate };
});

vi.mock("@/components/branding/Logo", () => ({
	Logo: ({ alt = "Bifrost" }: { alt?: string }) => <img alt={alt} />,
}));

vi.mock("@/lib/applicationName", () => ({
	useApplicationName: () => "Example Portal",
}));

import { CliAuthorizePage } from "./CliAuthorizePage";

const pendingResponse = {
	ok: true,
	status: 200,
	json: async () => ({
		transaction_id: "txn-1",
		client_name: "Bifrost CLI",
		redirect_uri: "http://127.0.0.1:49152/callback",
		expires_in: 240,
	}),
};

beforeEach(() => {
	mockNavigate.mockReset();
	authState.isAuthenticated = true;
	localStorage.clear();
	document.cookie = "csrf_token=; Max-Age=0; path=/";
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe("CliAuthorizePage", () => {
	it("inspects but does not authorize a request on page load", async () => {
		const fetchMock = vi.fn(
			async (
				_input: RequestInfo | URL,
				_init?: RequestInit,
			): Promise<Response> => pendingResponse as unknown as Response,
		);
		vi.stubGlobal("fetch", fetchMock);

		renderWithProviders(<CliAuthorizePage />, {
			initialEntries: ["/cli/authorize?transaction_id=txn-1"],
		});

		expect(
			await screen.findByRole("heading", {
				name: /authorize bifrost cli/i,
			}),
		).toBeInTheDocument();
		expect(
			screen.getByText("http://127.0.0.1:49152/callback"),
		).toBeInTheDocument();
		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect(fetchMock.mock.calls[0][0]).toContain(
			"/auth/cli/authorize?transaction_id=txn-1",
		);
		expect(fetchMock.mock.calls[0][1] ?? {}).not.toHaveProperty(
			"method",
			"POST",
		);
	});

	it("requires an explicit click and sends the CSRF-bound confirmation", async () => {
		localStorage.setItem("bifrost_access_token", "access-token");
		document.cookie = "csrf_token=csrf-1; path=/";
		const redirectTo = vi.fn();
		const fetchMock = vi
			.fn(
				async (
					_input: RequestInfo | URL,
					_init?: RequestInit,
				): Promise<Response> => pendingResponse as unknown as Response,
			)
			.mockResolvedValueOnce(pendingResponse as unknown as Response)
			.mockResolvedValueOnce({
				ok: true,
				status: 200,
				json: async () => ({
					redirect_url:
						"http://127.0.0.1:49152/callback?code=code-1&state=state-1",
				}),
			} as unknown as Response);
		vi.stubGlobal("fetch", fetchMock);

		const { user } = renderWithProviders(
			<CliAuthorizePage redirectTo={redirectTo} />,
			{ initialEntries: ["/cli/authorize?transaction_id=txn-1"] },
		);
		await screen.findByText("http://127.0.0.1:49152/callback");

		await user.click(
			screen.getByRole("button", { name: /approve cli access/i }),
		);

		await waitFor(() => expect(redirectTo).toHaveBeenCalledTimes(1));
		const [url, options] = fetchMock.mock.calls[1];
		expect(url).toBe("/auth/cli/authorize");
		expect(options).toMatchObject({
			method: "POST",
			credentials: "same-origin",
			headers: {
				Authorization: "Bearer access-token",
				"Content-Type": "application/json",
				"X-CSRF-Token": "csrf-1",
			},
			body: JSON.stringify({ transaction_id: "txn-1" }),
		});
		expect(redirectTo).toHaveBeenCalledWith(
			"http://127.0.0.1:49152/callback?code=code-1&state=state-1",
		);
	});

	it.each(["localhost", "127.0.0.1"])(
		"accepts an explicit default port for the %s callback",
		async (hostname) => {
			const redirectTo = vi.fn();
			const redirectUrl = `http://${hostname}:80/callback?code=code-1&state=state-1`;
			const fetchMock = vi
				.fn(
					async (
						_input: RequestInfo | URL,
						_init?: RequestInit,
					): Promise<Response> =>
						pendingResponse as unknown as Response,
				)
				.mockResolvedValueOnce(pendingResponse as unknown as Response)
				.mockResolvedValueOnce({
					ok: true,
					status: 200,
					json: async () => ({ redirect_url: redirectUrl }),
				} as unknown as Response);
			vi.stubGlobal("fetch", fetchMock);

			const { user } = renderWithProviders(
				<CliAuthorizePage redirectTo={redirectTo} />,
				{ initialEntries: ["/cli/authorize?transaction_id=txn-1"] },
			);
			await screen.findByText("http://127.0.0.1:49152/callback");
			await user.click(
				screen.getByRole("button", { name: /approve cli access/i }),
			);

			await waitFor(() =>
				expect(redirectTo).toHaveBeenCalledWith(redirectUrl),
			);
		},
	);

	it("refuses a localhost callback without an explicit port", async () => {
		const redirectTo = vi.fn();
		const fetchMock = vi
			.fn(
				async (
					_input: RequestInfo | URL,
					_init?: RequestInit,
				): Promise<Response> => pendingResponse as unknown as Response,
			)
			.mockResolvedValueOnce(pendingResponse as unknown as Response)
			.mockResolvedValueOnce({
				ok: true,
				status: 200,
				json: async () => ({
					redirect_url: "http://localhost/callback?code=code-1",
				}),
			} as unknown as Response);
		vi.stubGlobal("fetch", fetchMock);

		const { user } = renderWithProviders(
			<CliAuthorizePage redirectTo={redirectTo} />,
			{ initialEntries: ["/cli/authorize?transaction_id=txn-1"] },
		);
		await screen.findByText("http://127.0.0.1:49152/callback");
		await user.click(
			screen.getByRole("button", { name: /approve cli access/i }),
		);

		expect(
			await screen.findByText(/not a safe localhost url/i),
		).toBeInTheDocument();
		expect(redirectTo).not.toHaveBeenCalled();
	});

	it("returns an unauthenticated visitor through login with the full request", async () => {
		authState.isAuthenticated = false;
		const fetchMock = vi.fn();
		vi.stubGlobal("fetch", fetchMock);

		renderWithProviders(<CliAuthorizePage />, {
			initialEntries: ["/cli/authorize?transaction_id=txn-1"],
		});

		await waitFor(() =>
			expect(mockNavigate).toHaveBeenCalledWith(
				"/login?returnTo=%2Fcli%2Fauthorize%3Ftransaction_id%3Dtxn-1",
				{ replace: true },
			),
		);
		expect(fetchMock).not.toHaveBeenCalled();
	});

	it("refuses a non-local callback even if the API response is compromised", async () => {
		const redirectTo = vi.fn();
		const fetchMock = vi
			.fn(
				async (
					_input: RequestInfo | URL,
					_init?: RequestInit,
				): Promise<Response> => pendingResponse as unknown as Response,
			)
			.mockResolvedValueOnce(pendingResponse as unknown as Response)
			.mockResolvedValueOnce({
				ok: true,
				status: 200,
				json: async () => ({
					redirect_url: "https://evil.example/callback",
				}),
			} as unknown as Response);
		vi.stubGlobal("fetch", fetchMock);

		const { user } = renderWithProviders(
			<CliAuthorizePage redirectTo={redirectTo} />,
			{ initialEntries: ["/cli/authorize?transaction_id=txn-1"] },
		);
		await screen.findByText("http://127.0.0.1:49152/callback");
		await user.click(
			screen.getByRole("button", { name: /approve cli access/i }),
		);

		expect(
			await screen.findByText(/not a safe localhost url/i),
		).toBeInTheDocument();
		expect(redirectTo).not.toHaveBeenCalled();
	});
});
