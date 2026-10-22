import { describe, expect, it, vi } from "vitest";
import { requestWebSocketTicket } from "./websocket-auth";

describe("SDK requestWebSocketTicket", () => {
	it("exchanges the bearer in an HTTPS header and returns an opaque ticket", async () => {
		const ticket = "a".repeat(43);
		const fetchImpl = vi.fn().mockResolvedValue({
			ok: true,
			status: 200,
			json: async () => ({ ticket }),
		});

		await expect(
			requestWebSocketTicket(
				"header.payload.signature",
				"https://api.example.test/base",
				fetchImpl,
			),
		).resolves.toBe(ticket);
		expect(fetchImpl).toHaveBeenCalledWith(
			"https://api.example.test/ws/ticket",
			expect.objectContaining({
				method: "POST",
				headers: expect.objectContaining({
					Authorization: "Bearer header.payload.signature",
				}),
			}),
		);
	});

	it("rejects a malformed ticket response", async () => {
		const fetchImpl = vi.fn().mockResolvedValue({
			ok: true,
			status: 200,
			json: async () => ({ ticket: "access.jwt" }),
		});

		await expect(
			requestWebSocketTicket("bearer", "", fetchImpl),
		).rejects.toThrow("WebSocket ticket response was invalid");
	});
});
