import { beforeEach, describe, expect, it, vi } from "vitest";
import {
	EMBED_TOKEN_KEY,
	ACCESS_TOKEN_KEY,
	clearAuthTokens,
	consumeEmbedTokenFromHash,
	getActiveToken,
} from "./auth-token";

const validJwt = "eyJhbGciOiJIUzI1NiJ9.eyJlbWJlZCI6dHJ1ZX0.signature";

function tokenFor(payload: Record<string, unknown>): string {
	return `header.${btoa(JSON.stringify(payload)).replace(/=/g, "")}.signature`;
}

describe("consumeEmbedTokenFromHash", () => {
	beforeEach(() => {
		clearAuthTokens();
		window.history.replaceState(null, "", "/");
		vi.restoreAllMocks();
	});

	it("stores iframe embed tokens that look like JWTs and strips the fragment", () => {
		vi.spyOn(window, "top", "get").mockReturnValue({} as Window);
		window.history.replaceState(null, "", `/#embed_token=${validJwt}`);

		consumeEmbedTokenFromHash();

		expect(sessionStorage.getItem(EMBED_TOKEN_KEY)).toBe(validJwt);
		expect(window.location.hash).toBe("");
	});

	it("rejects malformed embed token fragments", () => {
		vi.spyOn(window, "top", "get").mockReturnValue({} as Window);
		window.history.replaceState(null, "", "/#embed_token=not-a-jwt");

		consumeEmbedTokenFromHash();

		expect(sessionStorage.getItem(EMBED_TOKEN_KEY)).toBeNull();
		expect(window.location.hash).toBe("");
	});

	it("does not persist embed tokens in a top-level tab", () => {
		vi.spyOn(window, "top", "get").mockReturnValue(window);
		window.history.replaceState(null, "", `/#embed_token=${validJwt}`);

		consumeEmbedTokenFromHash();

		expect(sessionStorage.getItem(EMBED_TOKEN_KEY)).toBeNull();
		expect(window.location.hash).toBe("");
	});
});

describe("capability-only app embed route", () => {
	beforeEach(() => {
		clearAuthTokens();
		window.history.replaceState(null, "", "/embedded/apps/hmac/portal/reports");
	});

	it("rejects a route with no capability", () => {
		expect(getActiveToken()).toBeNull();
	});

	it("does not fall back to an ordinary signed-in session", () => {
		localStorage.setItem(ACCESS_TOKEN_KEY, "normal-session");
		expect(getActiveToken()).toBeNull();
	});

	it("rejects a capability bound to a different app", () => {
		sessionStorage.setItem(
			EMBED_TOKEN_KEY,
			tokenFor({
				embed: true,
				embed_kind: "app",
				grant: "hmac",
				app_id: "app-2",
				app_slug: "other",
			}),
		);
		expect(getActiveToken()).toBeNull();
	});

	it("accepts the HMAC capability bound to the routed app", () => {
		const token = tokenFor({
			embed: true,
			embed_kind: "app",
			grant: "hmac",
			app_id: "app-1",
			app_slug: "portal",
		});
		sessionStorage.setItem(EMBED_TOKEN_KEY, token);
		expect(getActiveToken()).toBe(token);
	});
});
