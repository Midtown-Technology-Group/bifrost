import { beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router";
import { renderWithProviders, screen } from "@/test-utils";

const auth = {
	isAuthenticated: true,
	isLoading: false,
	isPlatformAdmin: false,
	isOrgUser: false,
	hasRole: vi.fn(() => false),
};
const capability = { token: null as string | null };

vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/lib/auth-token", () => ({
	getAppEmbedCapability: () => capability.token,
}));
vi.mock("@/components/NoAccess", () => ({ NoAccess: () => <p>No access</p> }));

import { ProtectedRoute } from "./ProtectedRoute";

beforeEach(() => {
	auth.isAuthenticated = true;
	auth.isLoading = false;
	auth.isPlatformAdmin = false;
	auth.isOrgUser = false;
	auth.hasRole.mockReturnValue(false);
	capability.token = null;
});

describe("ProtectedRoute", () => {
	it("renders children after auth loads for an unrestricted route", () => {
		renderWithProviders(
			<ProtectedRoute>
				<div>Private content</div>
			</ProtectedRoute>,
		);

		expect(screen.getByText("Private content")).toBeInTheDocument();
	});

	it("shows no access for platform-admin-only routes", () => {
		renderWithProviders(
			<ProtectedRoute requirePlatformAdmin>
				<div>Private content</div>
			</ProtectedRoute>,
		);

		expect(screen.getByText("No access")).toBeInTheDocument();
		expect(screen.queryByText("Private content")).not.toBeInTheDocument();
	});

	it("allows org users and embed users into org routes", () => {
		auth.hasRole.mockReturnValue(true);

		renderWithProviders(
			<ProtectedRoute requireOrgUser>
				<div>Private content</div>
			</ProtectedRoute>,
		);

		expect(screen.getByText("Private content")).toBeInTheDocument();
	});

	it("denies an ordinary signed-in session on the app embed route", () => {
		auth.isOrgUser = true;
		renderWithProviders(
			<Routes>
				<Route
					path="/embedded/apps/hmac/:applicationId/*"
					element={
						<ProtectedRoute requireOrgUser requireAppEmbed>
							<div>Embedded content</div>
						</ProtectedRoute>
					}
				/>
			</Routes>,
			{ initialEntries: ["/embedded/apps/hmac/portal"] },
		);

		expect(screen.getByText("No access")).toBeInTheDocument();
		expect(screen.queryByText("Embedded content")).not.toBeInTheDocument();
	});

	it("allows a route-bound app embed capability", () => {
		capability.token = "bound-capability";
		auth.hasRole.mockReturnValue(true);
		renderWithProviders(
			<Routes>
				<Route
					path="/embedded/apps/hmac/:applicationId/*"
					element={
						<ProtectedRoute requireOrgUser requireAppEmbed>
							<div>Embedded content</div>
						</ProtectedRoute>
					}
				/>
			</Routes>,
			{ initialEntries: ["/embedded/apps/hmac/portal"] },
		);

		expect(screen.getByText("Embedded content")).toBeInTheDocument();
	});

	it("renders nothing while auth is loading", () => {
		auth.isLoading = true;

		renderWithProviders(
			<ProtectedRoute>
				<div>Private content</div>
			</ProtectedRoute>,
		);

		expect(
			screen.getByRole("status", { name: /loading access/i }),
		).toBeInTheDocument();
		expect(screen.queryByText("Private content")).not.toBeInTheDocument();
	});
});

it("does not show permission denial while signed out", () => {
	auth.isAuthenticated = false;
	auth.isPlatformAdmin = false;
	renderWithProviders(
		<ProtectedRoute requirePlatformAdmin>
			<p>Private content</p>
		</ProtectedRoute>,
	);
	expect(
		screen.getByRole("status", { name: "Opening sign in…" }),
	).toBeVisible();
	expect(screen.queryByText("No access")).not.toBeInTheDocument();
});
