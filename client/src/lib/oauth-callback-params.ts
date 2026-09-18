/**
 * Query params from the OAuth provider that are consumed by the callback flow
 * itself and must not be replayed as entity_id candidates.
 */
const RESERVED_CALLBACK_PARAMS = new Set([
	"code",
	"state",
	"error",
	"error_description",
]);

/**
 * Collect the provider's raw callback query params for the token-exchange
 * request so the backend can capture entity_id (for example QuickBooks
 * Online's `realmId`).
 */
export function collectCallbackUrlParams(
	searchParams: URLSearchParams,
): Record<string, string> {
	const params: Record<string, string> = {};
	searchParams.forEach((value, key) => {
		if (!RESERVED_CALLBACK_PARAMS.has(key)) {
			params[key] = value;
		}
	});
	return params;
}
