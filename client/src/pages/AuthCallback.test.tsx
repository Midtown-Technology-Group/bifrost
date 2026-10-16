import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";
import { render, screen, waitFor } from "@testing-library/react";

import { hashOAuthState } from "@/services/auth";
import { AuthCallback } from "./AuthCallback";

const loginWithOAuth = vi.fn();
const navigate = vi.fn();

vi.mock("@/contexts/OrgScopeContext", () => ({
	useOrgScope: () => ({
		applicationName: "Bifrost",
		brandingLoaded: true,
		logoLoaded: true,
	}),
}));

vi.mock("react-router", async () => {
	const actual =
		await vi.importActual<typeof import("react-router")>("react-router");
	return {
		...actual,
		useNavigate: () => navigate,
	};
});

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({
		loginWithOAuth,
	}),
}));

describe("AuthCallback", () => {
	beforeEach(() => {
		loginWithOAuth.mockReset();
		loginWithOAuth.mockResolvedValue(undefined);
		navigate.mockReset();
		sessionStorage.clear();
	});

	it("rejects a callback when no browser OAuth state is stored", async () => {
		const getItem = vi.spyOn(Storage.prototype, "getItem");
		const removeItem = vi.spyOn(Storage.prototype, "removeItem");

		render(
			<MemoryRouter
				initialEntries={[
					"/auth/callback/microsoft?code=auth-code&state=server-state",
				]}
			>
				<Routes>
					<Route
						path="/auth/callback/:provider"
						element={<AuthCallback />}
					/>
				</Routes>
			</MemoryRouter>,
		);

		expect(
			await screen.findByText(
				"This sign-in session is missing or expired. Please start sign-in again.",
			),
		).toBeTruthy();
		expect(loginWithOAuth).not.toHaveBeenCalled();
		expect(navigate).not.toHaveBeenCalled();
		expect(getItem).toHaveBeenCalledWith("oauth_state");
		expect(removeItem).toHaveBeenCalledWith("oauth_state");
		expect(removeItem).toHaveBeenCalledWith("oauth_redirect_from");
	});

	it("exchanges the code only when the returned state matches", async () => {
		sessionStorage.setItem(
			"oauth_state",
			await hashOAuthState("server-state"),
		);

		render(
			<MemoryRouter
				initialEntries={[
					"/auth/callback/microsoft?code=auth-code&state=server-state",
				]}
			>
				<Routes>
					<Route
						path="/auth/callback/:provider"
						element={<AuthCallback />}
					/>
				</Routes>
			</MemoryRouter>,
		);

		await waitFor(() => {
			expect(loginWithOAuth).toHaveBeenCalledWith(
				"microsoft",
				"auth-code",
				"server-state",
			);
		});
		expect(navigate).toHaveBeenCalledWith("/", { replace: true });
	});

	it("rejects and clears a mismatched stored OAuth state", async () => {
		const removeItem = vi.spyOn(Storage.prototype, "removeItem");
		sessionStorage.setItem("oauth_state", "stale-client-state");

		render(
			<MemoryRouter
				initialEntries={[
					"/auth/callback/microsoft?code=auth-code&state=server-state",
				]}
			>
				<Routes>
					<Route
						path="/auth/callback/:provider"
						element={<AuthCallback />}
					/>
				</Routes>
			</MemoryRouter>,
		);

		expect(await screen.findByText("Invalid OAuth state")).toBeTruthy();
		expect(loginWithOAuth).not.toHaveBeenCalled();
		expect(removeItem).toHaveBeenCalledWith("oauth_state");
	});
});
