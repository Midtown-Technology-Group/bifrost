import { beforeEach, describe, expect, it, vi } from "vitest";
import { registerInviteWithPasskey, setupWithPasskey } from "./passkeys";
import { startRegistration } from "@simplewebauthn/browser";

vi.mock("@simplewebauthn/browser", () => ({
	browserSupportsWebAuthn: vi.fn(() => true),
	browserSupportsWebAuthnAutofill: vi.fn(() => Promise.resolve(true)),
	startAuthentication: vi.fn(),
	startRegistration: vi.fn(),
}));

describe("registerInviteWithPasskey", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		vi.mocked(startRegistration).mockResolvedValue({
			id: "credential-id",
		} as Awaited<ReturnType<typeof startRegistration>>);
	});

	it("creates a passkey from an invite token and returns login tokens", async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce({
				ok: true,
				json: async () => ({ options: { challenge: "abc" } }),
			})
			.mockResolvedValueOnce({
				ok: true,
				json: async () => ({
					user_id: "user-1",
					email: "invitee@example.com",
					access_token: "access",
					refresh_token: "refresh",
				}),
			});
		vi.stubGlobal("fetch", fetchMock);

		const result = await registerInviteWithPasskey("invite-token");

		expect(fetchMock).toHaveBeenNthCalledWith(
			1,
			"/auth/register-from-invite/passkey/options",
			expect.objectContaining({
				method: "POST",
				body: JSON.stringify({ token: "invite-token" }),
			}),
		);
		expect(startRegistration).toHaveBeenCalledWith({
			optionsJSON: { challenge: "abc" },
		});
		expect(fetchMock).toHaveBeenNthCalledWith(
			2,
			"/auth/register-from-invite/passkey/verify",
			expect.objectContaining({
				method: "POST",
				credentials: "same-origin",
				body: JSON.stringify({
					token: "invite-token",
					credential: { id: "credential-id" },
					device_name: undefined,
				}),
			}),
		);
		expect(result.access_token).toBe("access");
	});

	it("surfaces invite option errors before starting browser registration", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue({
				ok: false,
				json: async () => ({ detail: "Invite expired" }),
			}),
		);

		await expect(registerInviteWithPasskey("expired")).rejects.toThrow(
			"Invite expired",
		);
		expect(startRegistration).not.toHaveBeenCalled();
	});
});

describe("first-run passkey consent", () => {
	it.each([undefined, false, true])(
		"binds consent %s to the options request",
		async (consent) => {
			vi.mocked(startRegistration).mockResolvedValue({
				id: "credential-id",
			} as Awaited<ReturnType<typeof startRegistration>>);
			const fetchMock = vi
				.fn()
				.mockResolvedValueOnce({
					ok: true,
					json: async () => ({
						registration_token: "setup-token",
						options: { challenge: "abc" },
					}),
				})
				.mockResolvedValueOnce({
					ok: true,
					json: async () => ({ access_token: "access" }),
				});
			vi.stubGlobal("fetch", fetchMock);
			await setupWithPasskey(
				"first@example.com",
				"First",
				undefined,
				consent,
			);
			expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
				email: "first@example.com",
				name: "First",
				grant_secrets_reader: consent ?? false,
			});
			expect(
				JSON.parse(fetchMock.mock.calls[1][1].body),
			).not.toHaveProperty("grant_secrets_reader");
		},
	);
});
