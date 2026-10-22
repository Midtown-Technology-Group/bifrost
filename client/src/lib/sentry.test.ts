import { afterEach, describe, expect, it, vi } from "vitest";
import type { init as sentryInit } from "@sentry/react";
import { configureSentry } from "./sentry";

type SentryOptions = Parameters<typeof sentryInit>[0];

function configuredOptions(): SentryOptions {
	vi.stubEnv("VITE_SENTRY_DSN", "https://example@sentry.invalid/1");
	const sentry = { init: vi.fn<typeof sentryInit>() };
	expect(configureSentry(sentry)).toBe(true);
	return sentry.init.mock.calls[0][0];
}

describe("configureSentry", () => {
	afterEach(() => {
		vi.unstubAllEnvs();
		vi.restoreAllMocks();
	});

	it("does not initialize Sentry without a DSN", () => {
		vi.stubEnv("VITE_SENTRY_DSN", "");
		const sentry = { init: vi.fn() };

		expect(configureSentry(sentry)).toBe(false);
		expect(sentry.init).not.toHaveBeenCalled();
	});

	it("initializes Sentry from Vite env with privacy-first defaults", () => {
		vi.stubEnv("VITE_SENTRY_DSN", "https://example@sentry.invalid/1");
		vi.stubEnv("VITE_SENTRY_ENVIRONMENT", "testing");
		vi.stubEnv("VITE_SENTRY_RELEASE", "bifrost-client@1.2.3");
		vi.stubEnv("VITE_SENTRY_TRACES_SAMPLE_RATE", "0.25");
		const sentry = { init: vi.fn() };

		expect(configureSentry(sentry)).toBe(true);
		expect(sentry.init).toHaveBeenCalledWith(
			expect.objectContaining({
				beforeBreadcrumb: expect.any(Function),
				beforeSend: expect.any(Function),
				beforeSendSpan: expect.any(Function),
				beforeSendTransaction: expect.any(Function),
				dsn: "https://example@sentry.invalid/1",
				environment: "testing",
				release: "bifrost-client@1.2.3",
				sendDefaultPii: false,
				tracesSampleRate: 0.25,
			}),
		);
	});

	it("scrubs callback secrets from error events before transport", () => {
		const options = configuredOptions();
		const event = {
			type: undefined,
			request: {
				url: "https://portal.example/oauth/callback?code=secret-code&state=secret-state#token",
				query_string: "code=secret-code&state=secret-state",
				headers: {
					Referer:
						"https://portal.example/auth/callback?code=secret-code#token",
				},
			},
			breadcrumbs: [
				{
					category: "navigation",
					data: {
						from: "/login?returnTo=secret",
						to: "/auth/callback?code=secret-code&state=secret-state",
					},
				},
				{
					category: "fetch",
					data: {
						method: "GET",
						status_code: 200,
						url: "/mcp/callback?internal_state=secret-state",
					},
				},
			],
		};

		const scrubbed = options.beforeSend?.(event, {}) as typeof event;

		expect(scrubbed.request).toEqual({
			url: "https://portal.example/oauth/callback",
			headers: { Referer: "https://portal.example/auth/callback" },
		});
		expect(scrubbed.breadcrumbs).toEqual([
			{
				category: "navigation",
				data: { from: "/login", to: "/auth/callback" },
			},
			{
				category: "fetch",
				data: {
					method: "GET",
					status_code: 200,
					url: "/mcp/callback",
				},
			},
		]);
		expect(JSON.stringify(scrubbed)).not.toMatch(
			/secret-code|secret-state|token/,
		);
	});

	it("scrubs standalone breadcrumbs before they enter event scope", () => {
		const options = configuredOptions();
		const breadcrumb = {
			category: "xhr",
			data: {
				url: "/auth/callback?code=secret-code#secret-fragment",
				"url.query": "code=secret-code",
				method: "GET",
			},
		};

		expect(options.beforeBreadcrumb?.(breadcrumb)).toEqual({
			category: "xhr",
			data: { url: "/auth/callback", method: "GET" },
		});
	});

	it("scrubs transaction and span URL fields before transport", () => {
		const options = configuredOptions();
		const span = {
			data: {
				"http.method": "GET",
				"http.url":
					"https://portal.example/mcp/callback?internal_state=secret-state",
				"url.query": "internal_state=secret-state",
			},
			description: "GET /mcp/callback?internal_state=secret-state",
			span_id: "span",
			start_timestamp: 1,
			trace_id: "trace",
		};
		const standaloneSpan = {
			data: {
				"http.method": "GET",
				"http.url":
					"https://portal.example/oauth/callback?code=standalone-secret",
				"url.query": "code=standalone-secret",
			},
			description: "GET /oauth/callback?code=standalone-secret",
			span_id: "standalone-span",
			start_timestamp: 1,
			trace_id: "trace",
		};
		const transaction = {
			type: "transaction" as const,
			transaction: "/oauth/callback?code=secret-code",
			spans: [span],
			contexts: {
				trace: {
					data: {
						"url.full":
							"https://portal.example/oauth/callback?code=secret-code",
						"url.query": "code=secret-code",
					},
					span_id: "root-span",
					trace_id: "trace",
				},
			},
		};

		const scrubbedTransaction = options.beforeSendTransaction?.(
			transaction,
			{},
		) as typeof transaction;
		const scrubbedSpan = options.beforeSendSpan?.(standaloneSpan);

		expect(scrubbedTransaction.transaction).toBe("/oauth/callback");
		expect(scrubbedTransaction.spans[0]).toMatchObject({
			description: "GET /mcp/callback",
			data: {
				"http.method": "GET",
				"http.url": "https://portal.example/mcp/callback",
			},
		});
		expect(scrubbedTransaction.spans[0].data).not.toHaveProperty(
			"url.query",
		);
		expect(scrubbedTransaction.contexts.trace.data).toEqual({
			"url.full": "https://portal.example/oauth/callback",
		});
		expect(scrubbedSpan).toMatchObject({
			description: "GET /oauth/callback",
			data: {
				"http.method": "GET",
				"http.url": "https://portal.example/oauth/callback",
			},
		});
		expect(scrubbedSpan?.data).not.toHaveProperty("url.query");
		expect(
			JSON.stringify({ scrubbedTransaction, scrubbedSpan }),
		).not.toMatch(/secret-code|secret-state|standalone-secret/);
	});

	it("omits blank release values", () => {
		vi.stubEnv("VITE_SENTRY_DSN", "https://example@sentry.invalid/1");
		vi.stubEnv("VITE_SENTRY_RELEASE", "");
		const sentry = { init: vi.fn() };

		expect(configureSentry(sentry)).toBe(true);
		expect(sentry.init).toHaveBeenCalledWith(
			expect.not.objectContaining({ release: expect.anything() }),
		);
	});

	it("falls back to MODE when VITE_SENTRY_ENVIRONMENT is unset", () => {
		vi.stubEnv("VITE_SENTRY_DSN", "https://example@sentry.invalid/1");
		vi.stubEnv("MODE", "development");
		const sentry = { init: vi.fn() };

		expect(configureSentry(sentry)).toBe(true);
		expect(sentry.init).toHaveBeenCalledWith(
			expect.objectContaining({ environment: "development" }),
		);
	});

	it("falls back to MODE when VITE_SENTRY_ENVIRONMENT is blank", () => {
		vi.stubEnv("VITE_SENTRY_DSN", "https://example@sentry.invalid/1");
		vi.stubEnv("VITE_SENTRY_ENVIRONMENT", " ");
		vi.stubEnv("MODE", "development");
		const sentry = { init: vi.fn() };

		expect(configureSentry(sentry)).toBe(true);
		expect(sentry.init).toHaveBeenCalledWith(
			expect.objectContaining({ environment: "development" }),
		);
	});

	it("returns false when the SDK fails to initialize", () => {
		vi.stubEnv("VITE_SENTRY_DSN", "https://example@sentry.invalid/1");
		const warn = vi
			.spyOn(console, "warn")
			.mockImplementation(() => undefined);
		const sentry = {
			init: vi.fn(() => {
				throw new Error("bad DSN");
			}),
		};

		expect(configureSentry(sentry)).toBe(false);
		expect(warn).toHaveBeenCalledWith(
			"Sentry initialization failed; continuing without it",
			expect.any(Error),
		);
	});

	it("falls back to disabled tracing for invalid sample rates", () => {
		vi.stubEnv("VITE_SENTRY_DSN", "https://example@sentry.invalid/1");
		vi.stubEnv("VITE_SENTRY_TRACES_SAMPLE_RATE", "2");
		const sentry = { init: vi.fn() };

		expect(configureSentry(sentry)).toBe(true);
		expect(sentry.init).toHaveBeenCalledWith(
			expect.objectContaining({ tracesSampleRate: 0 }),
		);
	});

	it.each([
		["negative", "-0.5"],
		["NaN", "NaN"],
		["Infinity", "Infinity"],
		["empty", ""],
		["whitespace", "   "],
	])("falls back to 0 for %s sample rate", (_, value) => {
		vi.stubEnv("VITE_SENTRY_DSN", "https://example@sentry.invalid/1");
		vi.stubEnv("VITE_SENTRY_TRACES_SAMPLE_RATE", value);
		const sentry = { init: vi.fn() };

		expect(configureSentry(sentry)).toBe(true);
		expect(sentry.init).toHaveBeenCalledWith(
			expect.objectContaining({ tracesSampleRate: 0 }),
		);
	});
});
