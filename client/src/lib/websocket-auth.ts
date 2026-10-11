const TICKET_PATTERN = /^[A-Za-z0-9_-]{43}$/;

interface TicketResponse {
	ticket?: unknown;
}

/** Exchange a bearer token for a short-lived, single-use WebSocket ticket. */
export async function requestWebSocketTicket(
	token: string,
	baseUrl = "",
	fetchImpl: typeof fetch = fetch,
): Promise<string> {
	const endpoint = baseUrl
		? new URL("/ws/ticket", baseUrl).toString()
		: "/ws/ticket";
	const response = await fetchImpl(endpoint, {
		method: "POST",
		cache: "no-store",
		headers: {
			Accept: "application/json",
			Authorization: `Bearer ${token}`,
		},
	});
	if (!response.ok) {
		throw new Error(`WebSocket ticket request failed (${response.status})`);
	}
	const payload = (await response.json()) as TicketResponse;
	if (
		typeof payload.ticket !== "string" ||
		!TICKET_PATTERN.test(payload.ticket)
	) {
		throw new Error("WebSocket ticket response was invalid");
	}
	return payload.ticket;
}
