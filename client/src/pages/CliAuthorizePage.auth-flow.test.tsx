import { useEffect } from "react";
import { render, screen } from "@testing-library/react";
import {
	MemoryRouter,
	Route,
	Routes,
	useLocation,
	useNavigate,
} from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "@/contexts/AuthContext";

import { CliAuthorizePage } from "./CliAuthorizePage";

vi.mock("@/components/branding/Logo", () => ({
	Logo: () => <div data-testid="logo" />,
}));

vi.mock("@/lib/applicationName", () => ({
	useApplicationName: () => "Bifrost",
}));

const accessToken = [
	"eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0",
	btoa(
		JSON.stringify({
			sub: "user-1",
			email: "cli-user@example.com",
			exp: 4_102_444_800,
		}),
	)
		.replaceAll("+", "-")
		.replaceAll("/", "_")
		.replaceAll("=", ""),
	"signature",
].join(".");

function LoginProbe() {
	const { completeLoginWithToken, isLoading } = useAuth();
	const location = useLocation();
	const navigate = useNavigate();

	useEffect(() => {
		if (isLoading) return;

		const returnTo = new URLSearchParams(location.search).get("returnTo");
		completeLoginWithToken(accessToken);
		if (returnTo) {
			navigate(returnTo, { replace: true });
		}
	}, [completeLoginWithToken, isLoading, location.search, navigate]);

	return <p>Signing in</p>;
}

describe("CliAuthorizePage authentication flow", () => {
	afterEach(() => {
		vi.unstubAllGlobals();
		localStorage.clear();
		sessionStorage.clear();
	});

	it("preserves the transaction while a logged-out user signs in", async () => {
		const fetchMock = vi.fn(
			async (input: RequestInfo | URL, _init?: RequestInit) => {
				const url =
					typeof input === "string" ? input : input.toString();

				if (url === "/auth/status") {
					return Response.json({ needs_setup: false });
				}
				if (url.endsWith("/api/auth/refresh")) {
					return Response.json({}, { status: 401 });
				}
				if (url === "/auth/cli/authorize?transaction_id=txn-1") {
					return Response.json({
						transaction_id: "txn-1",
						client_name: "Bifrost CLI",
						redirect_uri: "http://127.0.0.1:8247/callback",
						expires_in: 240,
					});
				}

				throw new Error(`Unexpected fetch: ${url}`);
			},
		);
		vi.stubGlobal("fetch", fetchMock);

		render(
			<MemoryRouter
				initialEntries={["/cli/authorize?transaction_id=txn-1"]}
			>
				<AuthProvider>
					<Routes>
						<Route path="/login" element={<LoginProbe />} />
						<Route
							path="/cli/authorize"
							element={<CliAuthorizePage />}
						/>
					</Routes>
				</AuthProvider>
			</MemoryRouter>,
		);

		expect(
			await screen.findByRole("heading", {
				name: "Authorize Bifrost CLI",
			}),
		).toBeInTheDocument();
		expect(
			screen.getByText("http://127.0.0.1:8247/callback"),
		).toBeInTheDocument();
		expect(fetchMock).toHaveBeenCalledWith(
			"/auth/cli/authorize?transaction_id=txn-1",
			expect.any(Object),
		);
		expect(
			fetchMock.mock.calls.some(
				([input, init]) =>
					input === "/auth/cli/authorize" && init?.method === "POST",
			),
		).toBe(false);
	});
});
