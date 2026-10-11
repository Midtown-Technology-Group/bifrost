import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";
import { AuthProvider, useAuth } from "./AuthContext";
import { authFetch } from "@/lib/api-client";
import { ACCESS_TOKEN_KEY, EMBED_TOKEN_KEY } from "@/lib/auth-token";

afterEach(() => {
	vi.unstubAllGlobals();
	localStorage.clear();
	sessionStorage.clear();
});

it("shares cookie recovery between provider bootstrap and an initial API request", async () => {
	const token = `header.${btoa(JSON.stringify({ sub: "account", email: "account@example.test", roles: ["authenticated"], exp: Math.floor(Date.now() / 1000) + 3600 }))}.signature`;
	let release!: () => void;
	const gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	const refresh = vi.fn(async () => {
		await gate;
		return Response.json({ access_token: token });
	});
	vi.stubGlobal(
		"fetch",
		vi.fn(async (input: RequestInfo | URL) => {
			const url =
				typeof input === "string"
					? input
					: input instanceof URL
						? input.href
						: input.url;
			if (url.endsWith("/auth/status"))
				return Response.json({ needs_setup: false });
			if (url.endsWith("/api/auth/refresh")) return refresh();
			return Response.json({ ok: true });
		}),
	);
	function Probe() {
		const auth = useAuth();
		return (
			<p>
				{auth.isLoading
					? "Checking session"
					: (auth.user?.email ?? "Signed out")}
			</p>
		);
	}
	render(
		<MemoryRouter>
			<AuthProvider>
				<Probe />
			</AuthProvider>
		</MemoryRouter>,
	);
	await vi.waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
	const apiRequest = authFetch("/api/profile");
	await act(async () => {
		release();
		await apiRequest;
	});
	expect(refresh).toHaveBeenCalledTimes(1);
	expect(await screen.findByText("account@example.test")).toBeVisible();
	expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe(token);
});

it("revokes the browser session before clearing local authentication state", async () => {
	const token = `header.${btoa(JSON.stringify({ sub: "account", email: "account@example.test", roles: ["authenticated"], exp: Math.floor(Date.now() / 1000) + 3600 }))}.signature`;
	localStorage.setItem(ACCESS_TOKEN_KEY, token);
	let tokenDuringLogout: string | null = null;
	let logoutMethod: string | null = null;
	let logoutAuthorization: string | null = null;
	const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
		const url =
			input instanceof Request ? input.url : input.toString();
		if (url.endsWith("/auth/status")) {
			return Response.json({ needs_setup: false });
		}
		if (url.endsWith("/auth/logout")) {
			tokenDuringLogout = localStorage.getItem(ACCESS_TOKEN_KEY);
			logoutMethod = init?.method ?? null;
			logoutAuthorization = new Headers(init?.headers).get("Authorization");
			return Response.json({ message: "Logged out" });
		}
		return Response.json({ ok: true });
	});
	vi.stubGlobal("fetch", fetchMock);

	function Probe() {
		const auth = useAuth();
		return (
			<>
				<p>{auth.user?.email ?? "Signed out"}</p>
				<button type="button" onClick={auth.logout}>
					Log out
				</button>
			</>
		);
	}

	render(
		<MemoryRouter>
			<AuthProvider>
				<Probe />
			</AuthProvider>
		</MemoryRouter>,
	);

	await screen.findByText("account@example.test");
	// A caller-controlled embed marker must not bypass refresh-session revocation.
	sessionStorage.setItem(EMBED_TOKEN_KEY, "attacker-controlled-marker");
	fireEvent.click(screen.getByRole("button", { name: "Log out" }));

	expect(await screen.findByText("Signed out")).toBeVisible();
	expect(tokenDuringLogout).toBe(token);
	expect(logoutMethod).toBe("POST");
	expect(logoutAuthorization).toBe(`Bearer ${token}`);
	expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBeNull();
	expect(
		fetchMock.mock.calls.some(([input]) => {
			const url = input instanceof Request ? input.url : input.toString();
			return url.endsWith("/auth/logout");
		}),
	).toBe(true);
});

it("refreshes an expired browser session before retrying logout", async () => {
	const initialToken = `header.${btoa(JSON.stringify({ sub: "account", email: "account@example.test", roles: ["authenticated"], exp: Math.floor(Date.now() / 1000) + 3600 }))}.signature`;
	const refreshedToken = `header.${btoa(JSON.stringify({ sub: "account", email: "account@example.test", roles: ["authenticated"], exp: Math.floor(Date.now() / 1000) + 7200 }))}.signature`;
	localStorage.setItem(ACCESS_TOKEN_KEY, initialToken);
	const logoutAuthorizations: Array<string | null> = [];
	vi.stubGlobal(
		"fetch",
		vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
			const url =
				input instanceof Request ? input.url : input.toString();
			if (url.endsWith("/auth/status"))
				return Response.json({ needs_setup: false });
			if (url.endsWith("/api/auth/refresh"))
				return Response.json({ access_token: refreshedToken });
			if (url.endsWith("/auth/logout")) {
				logoutAuthorizations.push(
					new Headers(init?.headers).get("Authorization"),
				);
				return logoutAuthorizations.length === 1
					? Response.json({ detail: "Expired" }, { status: 401 })
					: Response.json({ message: "Logged out" });
			}
			return Response.json({ ok: true });
		}),
	);

	function Probe() {
		const auth = useAuth();
		return (
			<>
				<p>{auth.user?.email ?? "Signed out"}</p>
				<button type="button" onClick={auth.logout}>
					Log out
				</button>
			</>
		);
	}

	render(
		<MemoryRouter>
			<AuthProvider>
				<Probe />
			</AuthProvider>
		</MemoryRouter>,
	);

	await screen.findByText("account@example.test");
	fireEvent.click(screen.getByRole("button", { name: "Log out" }));

	expect(await screen.findByText("Signed out")).toBeVisible();
	expect(logoutAuthorizations).toEqual([
		`Bearer ${initialToken}`,
		`Bearer ${refreshedToken}`,
	]);
	expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBeNull();
});

it("keeps the local session when server-side logout fails", async () => {
	const token = `header.${btoa(JSON.stringify({ sub: "account", email: "account@example.test", roles: ["authenticated"], exp: Math.floor(Date.now() / 1000) + 3600 }))}.signature`;
	localStorage.setItem(ACCESS_TOKEN_KEY, token);
	vi.spyOn(console, "error").mockImplementation(() => undefined);
	vi.stubGlobal(
		"fetch",
		vi.fn(async (input: RequestInfo | URL) => {
			const url =
				input instanceof Request ? input.url : input.toString();
			if (url.endsWith("/auth/status")) {
				return Response.json({ needs_setup: false });
			}
			if (url.endsWith("/auth/logout")) {
				return Response.json({ detail: "Unavailable" }, { status: 503 });
			}
			return Response.json({ ok: true });
		}),
	);

	function Probe() {
		const auth = useAuth();
		return (
			<>
				<p>{auth.user?.email ?? "Signed out"}</p>
				<button type="button" onClick={auth.logout}>
					Log out
				</button>
			</>
		);
	}

	render(
		<MemoryRouter>
			<AuthProvider>
				<Probe />
			</AuthProvider>
		</MemoryRouter>,
	);

	await screen.findByText("account@example.test");
	fireEvent.click(screen.getByRole("button", { name: "Log out" }));
	await vi.waitFor(() =>
		expect(console.error).toHaveBeenCalledWith("Server logout failed"),
	);
	expect(screen.getByText("account@example.test")).toBeVisible();
	expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe(token);
});
