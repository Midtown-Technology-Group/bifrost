import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiClient, authFetch } from "./api-client";
import { ACCESS_TOKEN_KEY } from "./auth-token";

interface TestPlatformAuthBridge {
	getAccessToken: () => string | null;
	canRefreshAccessToken: () => boolean;
	refreshAccessToken: () => Promise<boolean>;
	handleAuthenticationFailure: () => void;
}

type TestPlatformAuthGlobal = typeof globalThis & {
	__BIFROST_PLATFORM_AUTH_V1__?: TestPlatformAuthBridge;
};

/**
 * Build a mock Response with a given status and an empty body.
 */
function mockResponse(status: number): Response {
	return new Response(null, { status });
}

/**
 * Build a never-expiring access token so `ensureValidToken` short-circuits
 * without actually hitting the refresh endpoint.
 *
 * JWT shape: header.payload.signature (signature is unverified by the
 * client). The payload is `{exp: <far-future>}`.
 */
function buildFakeToken(): string {
	const farFuture = Math.floor(Date.now() / 1000) + 60 * 60 * 24 * 365;
	const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
	const payload = btoa(JSON.stringify({ exp: farFuture }));
	return `${header}.${payload}.sig`;
}

describe("V2 app platform auth bridge", () => {
	afterEach(() => {
		vi.unstubAllGlobals();
		localStorage.clear();
		sessionStorage.clear();
	});

	it("shares one refresh request across concurrent SDK recovery calls", async () => {
		const refreshedToken = buildFakeToken();
		let releaseRefresh!: () => void;
		const refreshGate = new Promise<void>((resolve) => {
			releaseRefresh = resolve;
		});
		const fetchMock = vi.fn(async () => {
			await refreshGate;
			return mockJsonResponse(200, { access_token: refreshedToken });
		});
		vi.stubGlobal("fetch", fetchMock);
		const bridge = (globalThis as TestPlatformAuthGlobal)
			.__BIFROST_PLATFORM_AUTH_V1__;
		expect(bridge).toBeDefined();

		const first = bridge!.refreshAccessToken();
		const second = bridge!.refreshAccessToken();
		expect(fetchMock).toHaveBeenCalledTimes(1);
		releaseRefresh();

		await expect(Promise.all([first, second])).resolves.toEqual([
			true,
			true,
		]);
		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect(bridge!.getAccessToken()).toBe(refreshedToken);
	});
});

/**
 * Drain the microtask queue + advance fake timers so any pending setTimeout
 * for retry backoff fires. Repeated multiple times to flush
 * await-then-setTimeout chains.
 */
async function flushRetries(maxBackoffMs: number = 5000): Promise<void> {
	// Run microtasks so awaited fetch promises resolve before timers tick.
	for (let i = 0; i < 5; i++) {
		await Promise.resolve();
	}
	await vi.advanceTimersByTimeAsync(maxBackoffMs);
}

describe("authFetch transient 5xx retry", () => {
	let fetchMock: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		// Seed a valid access token so ensureValidToken passes without a
		// refresh call.
		localStorage.setItem(ACCESS_TOKEN_KEY, buildFakeToken());

		fetchMock = vi.fn();
		vi.stubGlobal("fetch", fetchMock);

		// Use fake timers so the backoff sleeps don't actually delay tests.
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
		localStorage.clear();
		sessionStorage.clear();
	});

	it("retries a GET that returns 503 twice then 200", async () => {
		fetchMock
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockResponse(200));

		const promise = authFetch("/api/test");
		await flushRetries();
		const response = await promise;

		expect(response.status).toBe(200);
		expect(fetchMock).toHaveBeenCalledTimes(3);
	});

	it("does not retry a POST that returns 503", async () => {
		fetchMock.mockResolvedValueOnce(mockResponse(503));

		const promise = authFetch("/api/test", { method: "POST" });
		await flushRetries();
		const response = await promise;

		expect(response.status).toBe(503);
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});

	it("returns the last 503 after a PUT exhausts all retries", async () => {
		fetchMock
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockResponse(503));

		const promise = authFetch("/api/test", {
			method: "PUT",
			body: JSON.stringify({ x: 1 }),
		});
		await flushRetries();
		const response = await promise;

		expect(response.status).toBe(503);
		// 1 initial attempt + 3 retries
		expect(fetchMock).toHaveBeenCalledTimes(4);
	});

	it("does not retry a GET that returns 200 immediately", async () => {
		fetchMock.mockResolvedValueOnce(mockResponse(200));

		const promise = authFetch("/api/test");
		await flushRetries();
		const response = await promise;

		expect(response.status).toBe(200);
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});

	it("retries a GET through 502 then 503 then resolves to 200", async () => {
		fetchMock
			.mockResolvedValueOnce(mockResponse(502))
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockResponse(200));

		const promise = authFetch("/api/test");
		await flushRetries();
		const response = await promise;

		expect(response.status).toBe(200);
		expect(fetchMock).toHaveBeenCalledTimes(3);
	});

	it("retries a DELETE on 504 and resolves to 204", async () => {
		fetchMock
			.mockResolvedValueOnce(mockResponse(504))
			.mockResolvedValueOnce(mockResponse(204));

		const promise = authFetch("/api/test", { method: "DELETE" });
		await flushRetries();
		const response = await promise;

		expect(response.status).toBe(204);
		expect(fetchMock).toHaveBeenCalledTimes(2);
	});

	it("does not retry a GET that returns 500 (not in TRANSIENT_5XX)", async () => {
		fetchMock.mockResolvedValueOnce(mockResponse(500));

		const promise = authFetch("/api/test");
		await flushRetries();
		const response = await promise;

		expect(response.status).toBe(500);
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});

	it("retries a HEAD on 503 and resolves to 200", async () => {
		fetchMock
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockResponse(200));

		const promise = authFetch("/api/test", { method: "HEAD" });
		await flushRetries();
		const response = await promise;

		expect(response.status).toBe(200);
		expect(fetchMock).toHaveBeenCalledTimes(2);
	});
});

/**
 * Build a JSON Response with the given status and body.
 *
 * openapi-fetch tries to parse the response body when content-type is JSON,
 * so 200 responses need a real JSON payload — even an empty object — or
 * the parse step will throw.
 */
function mockJsonResponse(status: number, body: unknown = {}): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { "Content-Type": "application/json" },
	});
}

describe("apiClient (openapi-fetch middleware) transient 5xx retry", () => {
	let fetchMock: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		// Seed a valid access token so the middleware's ensureValidToken
		// short-circuits without trying to refresh.
		localStorage.setItem(ACCESS_TOKEN_KEY, buildFakeToken());

		fetchMock = vi.fn();
		// openapi-fetch captures `globalThis.fetch` at createClient() time
		// (so module load), so stubGlobal alone won't hit the initial
		// request. The retry path inside the middleware DOES call global
		// `fetch`, which we still want intercepted — but we also pass
		// `fetch: fetchMock` per-call below so the initial request goes
		// through the same mock.
		vi.stubGlobal("fetch", fetchMock);

		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
		localStorage.clear();
		sessionStorage.clear();
	});

	it("apiClient.GET retries a transient 5xx and returns the eventual 200", async () => {
		fetchMock
			.mockResolvedValueOnce(mockResponse(503))
			.mockResolvedValueOnce(mockJsonResponse(200, { version: "1.0" }));

		// `fetch` per-call override is supported by openapi-fetch's
		// coreFetch (`fetch = baseFetch` at line 48 of node_modules/openapi-fetch/src/index.js).
		// Cast to any since the typed FetchOptions doesn't surface the
		// override field in the public types but it's implemented in core.
		const promise = apiClient.GET("/api/version", {
			fetch: fetchMock,
		} as never);
		await flushRetries();
		const { response } = await promise;

		expect(response.status).toBe(200);
		expect(fetchMock).toHaveBeenCalledTimes(2);
	});

	it("apiClient.PUT replays the body across retries via the WeakMap clone cache", async () => {
		// Capture the body sent on each fetch call. The body is a stream so
		// we read it eagerly in the mock impl before resolving.
		const capturedBodies: string[] = [];
		fetchMock.mockImplementation(async (req: Request) => {
			capturedBodies.push(await req.clone().text());
			// First attempt 503, second attempt 200.
			if (capturedBodies.length === 1) return mockResponse(503);
			return mockJsonResponse(200, {});
		});

		const promise = apiClient.PUT("/api/branding", {
			body: { primary_color: "#abcdef" },
			// See note above re: per-call fetch override + cast.
			fetch: fetchMock,
		} as never);
		await flushRetries();
		const { response } = await promise;

		expect(response.status).toBe(200);
		expect(fetchMock).toHaveBeenCalledTimes(2);
		// Both attempts must have carried the same body — proves the
		// WeakMap-cached pre-send clone replayed correctly.
		expect(capturedBodies).toHaveLength(2);
		expect(JSON.parse(capturedBodies[0])).toEqual({
			primary_color: "#abcdef",
		});
		expect(JSON.parse(capturedBodies[1])).toEqual({
			primary_color: "#abcdef",
		});
	});
});

describe.each(["authFetch", "apiClient"] as const)(
	"%s session versus route denial",
	(kind) => {
		let fetchMock: ReturnType<typeof vi.fn>;
		let token: string;
		beforeEach(() => {
			token = buildFakeToken();
			localStorage.setItem(ACCESS_TOKEN_KEY, token);
			window.history.replaceState(null, "", "/login");
			fetchMock = vi.fn();
			vi.stubGlobal("fetch", fetchMock);
		});
		afterEach(() => {
			vi.unstubAllGlobals();
			localStorage.clear();
			sessionStorage.clear();
			window.history.replaceState(null, "", "/");
		});
		const call = async () =>
			kind === "authFetch"
				? authFetch("http://localhost/api/version")
				: (
						await apiClient.GET("/api/version", {
							fetch: fetchMock,
						} as never)
					).response;
		it("keeps a valid session when one route returns 401", async () => {
			fetchMock
				.mockResolvedValueOnce(mockResponse(401))
				.mockResolvedValueOnce(mockJsonResponse(200, { id: "user" }));
			expect((await call()).status).toBe(401);
			expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe(token);
			expect(fetchMock).toHaveBeenCalledTimes(2);
			expect(fetchMock.mock.calls[1][0]).toBe("/auth/me");
		});
		it("leaves 403 permission failures to the page", async () => {
			fetchMock.mockResolvedValueOnce(mockResponse(403));
			expect((await call()).status).toBe(403);
			expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe(token);
			expect(fetchMock).toHaveBeenCalledTimes(1);
		});
		it("does not sign out when session verification is unavailable", async () => {
			fetchMock
				.mockResolvedValueOnce(mockResponse(401))
				.mockRejectedValueOnce(new TypeError("Network unavailable"));
			expect((await call()).status).toBe(401);
			expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe(token);
		});
		it("refreshes and retries when identity also rejects the token", async () => {
			fetchMock
				.mockResolvedValueOnce(mockResponse(401))
				.mockResolvedValueOnce(mockResponse(401))
				.mockResolvedValueOnce(
					mockJsonResponse(200, { access_token: token }),
				)
				.mockResolvedValueOnce(
					mockJsonResponse(200, { version: "1.0" }),
				);
			expect((await call()).status).toBe(200);
			expect(fetchMock.mock.calls[2][0]).toBe("/api/auth/refresh");
			expect(fetchMock).toHaveBeenCalledTimes(4);
		});
		it("clears a session rejected by both identity and refresh", async () => {
			fetchMock.mockImplementation(async () => mockResponse(401));
			expect((await call()).status).toBe(401);
			expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBeNull();
		});
	},
);

describe("apiClient 401 refresh replay for bodied requests", () => {
	let fetchMock: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		localStorage.setItem(ACCESS_TOKEN_KEY, buildFakeToken());
		window.history.replaceState(null, "", "/login");
		fetchMock = vi.fn();
		vi.stubGlobal("fetch", fetchMock);
	});

	afterEach(() => {
		vi.unstubAllGlobals();
		localStorage.clear();
		sessionStorage.clear();
		window.history.replaceState(null, "", "/");
	});

	/**
	 * A real `fetch()` consumes the request body before the middleware ever
	 * sees the 401, so the replay in `handleAuthResponse` must be built from
	 * the pre-send clone — not from the Request fetch already read.
	 *
	 * This is the exact path `POST /api/workflows/execute` takes when a page
	 * refresh races token expiry: the setup-status query dies here, and the
	 * app's cards stay on their empty ("Not Connected") state even though a
	 * direct execution of the same workflow succeeds.
	 */
	it("replays the POST body and fresh token after a 401 refresh retry", async () => {
		// happy-dom's Request tolerates re-sending a body `fetch` already
		// consumed; real engines do not — browsers drop the body (the replay
		// reaches the server as an empty POST) and Node throws
		// "Cannot construct a Request with a Request whose body is used".
		// Model that contract so the replay can't regress onto the live,
		// already-sent Request.
		const RealRequest = globalThis.Request;
		class StrictRequest extends RealRequest {
			constructor(input: RequestInfo | URL, init?: RequestInit) {
				if (input instanceof RealRequest && input.bodyUsed) {
					throw new TypeError(
						"Cannot construct a Request with a Request whose body is used",
					);
				}
				super(input, init);
			}
		}
		vi.stubGlobal("Request", StrictRequest);

		const refreshedToken = (() => {
			const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
			const payload = btoa(
				JSON.stringify({
					exp: Math.floor(Date.now() / 1000) + 60 * 60,
				}),
			);
			return `${header}.${payload}.refreshed`;
		})();

		let executeAttempts = 0;
		const replayed: { body: string; auth: string | null }[] = [];

		fetchMock.mockImplementation(async (input: Request | string) => {
			// Identity + refresh calls are issued as plain URL strings.
			if (typeof input === "string") {
				if (input === "/auth/me") return mockResponse(401);
				if (input === "/api/auth/refresh")
					return mockJsonResponse(200, {
						access_token: refreshedToken,
					});
				return mockResponse(404);
			}
			const pathname = new URL(input.url).pathname;
			if (pathname === "/auth/me") return mockResponse(401);
			if (pathname === "/api/auth/refresh")
				return mockJsonResponse(200, { access_token: refreshedToken });
			if (pathname === "/api/workflows/execute") {
				const body = await input.text();
				executeAttempts += 1;
				if (executeAttempts === 1) return mockResponse(401);
				replayed.push({
					body,
					auth: input.headers.get("Authorization"),
				});
				return mockJsonResponse(200, {
					execution_id: "exec-setup-1",
					status: "Success",
				});
			}
			return mockResponse(404);
		});

		const { data, response } = await apiClient.POST(
			"/api/workflows/execute",
			{
				body: {
					workflow_id: "check_microsoft_setup",
					input_data: {},
				},
				fetch: fetchMock,
			} as never,
		);

		expect(response.status).toBe(200);
		expect(executeAttempts).toBe(2);
		expect(replayed).toHaveLength(1);
		expect(JSON.parse(replayed[0].body)).toEqual({
			workflow_id: "check_microsoft_setup",
			input_data: {},
		});
		expect(replayed[0].auth).toBe(`Bearer ${refreshedToken}`);
		expect(data).toEqual({
			execution_id: "exec-setup-1",
			status: "Success",
		});
	});
});
