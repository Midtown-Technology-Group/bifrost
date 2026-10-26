import * as Sentry from "@sentry/react";

type SentryModule = Pick<typeof Sentry, "init">;
type SentryOptions = Parameters<typeof Sentry.init>[0];
type SentryBreadcrumb = Parameters<
	NonNullable<SentryOptions["beforeBreadcrumb"]>
>[0];
type SentryErrorEvent = Parameters<NonNullable<SentryOptions["beforeSend"]>>[0];
type SentryTransactionEvent = Parameters<
	NonNullable<SentryOptions["beforeSendTransaction"]>
>[0];
type SentryEvent = SentryErrorEvent | SentryTransactionEvent;
type SentrySpan = Parameters<NonNullable<SentryOptions["beforeSendSpan"]>>[0];

const URL_DATA_KEYS = new Set([
	"from",
	"http.target",
	"http.url",
	"request.url",
	"request_url",
	"to",
	"url",
	"url.full",
	"url.original",
]);
const URL_COMPONENT_KEYS = new Set([
	"http.fragment",
	"http.query",
	"url.fragment",
	"url.query",
]);

function stripUrlSecrets(value: string): string {
	const boundaries = [value.indexOf("?"), value.indexOf("#")].filter(
		(index) => index >= 0,
	);
	return boundaries.length === 0
		? value
		: value.slice(0, Math.min(...boundaries));
}

function scrubUrlData(data: Record<string, unknown> | undefined): void {
	if (!data) return;

	for (const [key, value] of Object.entries(data)) {
		const normalizedKey = key.toLowerCase();
		if (URL_COMPONENT_KEYS.has(normalizedKey)) {
			delete data[key];
		} else if (
			typeof value === "string" &&
			(URL_DATA_KEYS.has(normalizedKey) ||
				normalizedKey.endsWith(".url") ||
				normalizedKey.endsWith("_url"))
		) {
			data[key] = stripUrlSecrets(value);
		}
	}
}

function scrubBreadcrumb(breadcrumb: SentryBreadcrumb): SentryBreadcrumb {
	scrubUrlData(breadcrumb.data);
	return breadcrumb;
}

function scrubSpan(span: SentrySpan): SentrySpan {
	if (span.description) {
		span.description = stripUrlSecrets(span.description);
	}
	scrubUrlData(span.data);
	return span;
}

function scrubRequest(event: SentryEvent): void {
	const request = event.request;
	if (!request) return;

	if (request.url) {
		request.url = stripUrlSecrets(request.url);
	}
	delete request.query_string;
	if (request.headers) {
		for (const [key, value] of Object.entries(request.headers)) {
			if (["referer", "referrer"].includes(key.toLowerCase())) {
				request.headers[key] = stripUrlSecrets(value);
			}
		}
	}
}

function scrubEvent<T extends SentryEvent>(event: T): T {
	scrubRequest(event);
	if (event.transaction) {
		event.transaction = stripUrlSecrets(event.transaction);
	}
	for (const breadcrumb of event.breadcrumbs ?? []) {
		scrubBreadcrumb(breadcrumb);
	}
	for (const span of event.spans ?? []) {
		scrubSpan(span);
	}
	scrubUrlData(event.contexts?.trace?.data);
	return event;
}

function parseSampleRate(value: string | undefined): number {
	if (value === undefined || value.trim() === "") {
		return 0;
	}

	const parsed = Number(value);
	if (!Number.isFinite(parsed) || parsed < 0 || parsed > 1) {
		return 0;
	}

	return parsed;
}

export function configureSentry(sentryModule: SentryModule = Sentry): boolean {
	const dsn = import.meta.env.VITE_SENTRY_DSN;
	if (!dsn) {
		return false;
	}

	const release = import.meta.env.VITE_SENTRY_RELEASE?.trim();
	const environment =
		import.meta.env.VITE_SENTRY_ENVIRONMENT?.trim() || import.meta.env.MODE;
	const options: SentryOptions = {
		beforeBreadcrumb: scrubBreadcrumb,
		beforeSend: scrubEvent,
		beforeSendSpan: scrubSpan,
		beforeSendTransaction: scrubEvent,
		dsn,
		environment,
		sendDefaultPii: false,
		tracesSampleRate: parseSampleRate(
			import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE,
		),
	};
	if (release) {
		options.release = release;
	}

	try {
		sentryModule.init(options);
	} catch (error) {
		console.warn(
			"Sentry initialization failed; continuing without it",
			error,
		);
		return false;
	}

	return true;
}
