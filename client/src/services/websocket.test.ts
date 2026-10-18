import { afterEach, describe, expect, it, vi } from "vitest";
import {
	type PlatformJobUpdate,
	sanitizeLogText,
	webSocketService,
} from "./websocket";

describe("webSocketService security hardening", () => {
	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("sanitizes control characters before logging", () => {
		expect(sanitizeLogText("first\nsecond\rthird\tfourth")).toBe(
			"first second third fourth",
		);
	});

	it("logs notification receipt without dumping attacker-controlled payloads", () => {
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		const service = webSocketService as unknown as {
			handleMessage: (message: unknown) => void;
		};

		service.handleMessage({
			type: "notification_created",
			notification: {
				id: "note-1\nforged",
				category: "system",
				title: "title",
				description: "description",
				status: "running\rforged",
				percent: null,
				error: null,
				result: null,
				metadata: { untrusted: "\nforged" },
				created_at: "2026-05-21T00:00:00Z",
				updated_at: "2026-05-21T00:00:00Z",
				user_id: "user-1",
			},
		});

		expect(warnSpy).toHaveBeenCalledWith("[WS] Notification received");
		expect(warnSpy.mock.calls[0]).toHaveLength(1);
	});

	it("uses a one-time ticket rather than an embed JWT in the socket URL", async () => {
		const payload = btoa(JSON.stringify({ embed: true }));
		const embedToken = `header.${payload}.signature`;
		const ticket = "a".repeat(43);
		sessionStorage.setItem("bifrost_embed_token", embedToken);
		const fetchImpl = vi.fn().mockResolvedValue({
			ok: true,
			status: 200,
			json: async () => ({ ticket }),
		});
		vi.stubGlobal("fetch", fetchImpl);

		class MockWebSocket {
			static urls: string[] = [];
			onopen: (() => void) | null = null;
			onmessage: (() => void) | null = null;
			onerror: (() => void) | null = null;
			onclose: (() => void) | null = null;

			constructor(url: string) {
				MockWebSocket.urls.push(url);
			}

			addEventListener(type: string, listener: () => void): void {
				if (type === "open") queueMicrotask(listener);
			}
		}
		vi.stubGlobal("WebSocket", MockWebSocket);
		const service = webSocketService as unknown as {
			_connect(channels: string[]): Promise<void>;
			ws: WebSocket | null;
		};

		try {
			await service._connect([]);
			const url = new URL(MockWebSocket.urls[0]);
			expect(url.searchParams.get("ticket")).toBe(ticket);
			expect(url.searchParams.has("token")).toBe(false);
			expect(url.toString()).not.toContain(embedToken);
			expect(fetchImpl).toHaveBeenCalledWith(
				"/ws/ticket",
				expect.objectContaining({
					headers: expect.objectContaining({
						Authorization: `Bearer ${embedToken}`,
					}),
				}),
			);
		} finally {
			service.ws = null;
			sessionStorage.removeItem("bifrost_embed_token");
			vi.unstubAllGlobals();
		}
	});

	it("retries ticket acquisition with the original channels", async () => {
		vi.useFakeTimers();
		const payload = btoa(JSON.stringify({ embed: true }));
		sessionStorage.setItem("bifrost_embed_token", `header.${payload}.signature`);
		vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ticket unavailable")));
		vi.spyOn(console, "error").mockImplementation(() => {});
		const service = webSocketService as unknown as {
			_connect(channels: string[]): Promise<void>;
			retryCount: number;
		};
		service.retryCount = 0;
		const connect = vi.spyOn(webSocketService, "connect").mockResolvedValue();
		try {
			await expect(service._connect(["execution:original"])).rejects.toThrow(
				"ticket unavailable",
			);
			await vi.advanceTimersByTimeAsync(2000);
			expect(connect).toHaveBeenCalledWith(["execution:original"]);
		} finally {
			await webSocketService.disconnect();
			service.retryCount = 0;
			sessionStorage.removeItem("bifrost_embed_token");
			vi.unstubAllGlobals();
			vi.useRealTimers();
		}
	});

	it.each(["resolve", "reject"] as const)(
		"disconnect invalidates a pending ticket request that later %ss",
		async (outcome) => {
			vi.useFakeTimers();
			const payload = btoa(JSON.stringify({ embed: true }));
			sessionStorage.setItem(
				"bifrost_embed_token",
				`header.${payload}.signature`,
			);
			let resolveTicket!: (response: Response) => void;
			let rejectTicket!: (error: Error) => void;
			const ticketRequest = new Promise<Response>((resolve, reject) => {
				resolveTicket = resolve;
				rejectTicket = reject;
			});
			const fetchImpl = vi.fn().mockReturnValue(ticketRequest);
			vi.stubGlobal("fetch", fetchImpl);
			class MockWebSocket {
				static urls: string[] = [];

				constructor(url: string) {
					MockWebSocket.urls.push(url);
				}

				addEventListener(type: string, listener: () => void): void {
					if (type === "open") queueMicrotask(listener);
				}
			}
			vi.stubGlobal("WebSocket", MockWebSocket);
			const service = webSocketService as unknown as {
				_connect(channels: string[]): Promise<void>;
				retryCount: number;
			};
			service.retryCount = 0;
			const reconnect = vi
				.spyOn(webSocketService, "connect")
				.mockResolvedValue();

			try {
				const pending = service._connect(["execution:cancelled"]);
				expect(fetchImpl).toHaveBeenCalledOnce();
				await webSocketService.disconnect();
				if (outcome === "resolve") {
					resolveTicket({
						ok: true,
						status: 200,
						json: async () => ({ ticket: "a".repeat(43) }),
					} as Response);
				} else {
					rejectTicket(new Error("ticket unavailable"));
				}

				await expect(pending).resolves.toBeUndefined();
				await vi.advanceTimersByTimeAsync(4000);
				expect(MockWebSocket.urls).toHaveLength(0);
				expect(reconnect).not.toHaveBeenCalled();
			} finally {
				await webSocketService.disconnect();
				service.retryCount = 0;
				sessionStorage.removeItem("bifrost_embed_token");
				vi.unstubAllGlobals();
				vi.useRealTimers();
			}
		},
	);

	it("disconnect invalidates a non-embed connection across ticket setup", async () => {
		sessionStorage.removeItem("bifrost_embed_token");
		class MockWebSocket {
			static urls: string[] = [];
			onopen: (() => void) | null = null;
			onmessage: (() => void) | null = null;
			onerror: (() => void) | null = null;
			onclose: (() => void) | null = null;

			constructor(url: string) {
				MockWebSocket.urls.push(url);
			}

			close(): void {}
		}
		vi.stubGlobal("WebSocket", MockWebSocket);
		const service = webSocketService as unknown as {
			_connect(channels: string[]): Promise<void>;
		};

		try {
			const pending = service._connect(["execution:cancelled"]);
			await webSocketService.disconnect();
			await pending;

			expect(MockWebSocket.urls).toHaveLength(0);
		} finally {
			await webSocketService.disconnect();
			vi.unstubAllGlobals();
		}
	});
});

describe("platform-job WebSocket contract", () => {
	it("dispatches the same durable snapshot by job id", () => {
		const callback = vi.fn();
		const unsubscribe = webSocketService.onPlatformJobUpdate(
			"job-1",
			callback,
		);
		const job = {
			id: "job-1",
			job_type: "application.publish",
			payload_version: 1,
			organization_id: null,
			resource_type: "application",
			resource_id: "app-1",
			resource_lock_key: null,
			priority: 100,
			title: "Publishing Test",
			action_url: "/apps/test/edit",
			execution_backend: "local",
			requested_by_user_id: "user-1",
			requested_by_name: "Dev",
			status: "running",
			progress: {
				phase: "promoting current bundle",
				current: 2,
				total: 3,
				percent: 66,
			},
			revision: 4,
			attempt: 1,
			max_attempts: 2,
			can_cancel: false,
			result: null,
			error: null,
			notification_id: "notification-1",
			started_at: "2026-07-28T12:00:00Z",
			completed_at: null,
			created_at: "2026-07-28T12:00:00Z",
			updated_at: "2026-07-28T12:00:01Z",
		} satisfies PlatformJobUpdate;

		(
			webSocketService as unknown as {
				handleMessage(message: unknown): void;
			}
		).handleMessage({ type: "platform_job_updated", job });

		expect(callback).toHaveBeenCalledOnce();
		expect(callback).toHaveBeenCalledWith(job);
		unsubscribe();
	});

	it("dispatches updates to global observers", () => {
		const callback = vi.fn();
		const unsubscribe = webSocketService.onAnyPlatformJobUpdate(callback);
		const job = {
			id: "job-2",
			job_type: "solution.deploy",
			payload_version: 1,
			organization_id: null,
			resource_type: "solution_deploy",
			resource_id: "deploy-1",
			resource_lock_key: null,
			priority: 500,
			title: "Solution deploy",
			action_url: "/solutions/solution-1",
			execution_backend: "local",
			requested_by_user_id: "user-1",
			requested_by_name: "Dev",
			status: "queued",
			progress: { phase: "Queued", current: 0, total: null, percent: 0 },
			revision: 1,
			attempt: 0,
			max_attempts: 2,
			can_cancel: true,
			result: null,
			error: null,
			notification_id: null,
			started_at: null,
			completed_at: null,
			created_at: "2026-08-23T12:00:00Z",
			updated_at: "2026-08-23T12:00:00Z",
		} satisfies PlatformJobUpdate;

		(
			webSocketService as unknown as {
				handleMessage(message: unknown): void;
			}
		).handleMessage({ type: "platform_job_updated", job });

		expect(callback).toHaveBeenCalledWith(job);
		unsubscribe();
	});
});

describe("chat WebSocket contract", () => {
	it("dispatches durable chat run envelopes by conversation", () => {
		const callback = vi.fn();
		const unsubscribe = webSocketService.onChatStream(
			"conversation-1",
			callback,
		);
		const event = {
			type: "chat_run_event",
			protocol_version: 1,
			event_id: "event-1",
			sequence: 2,
			conversation_id: "conversation-1",
			run_id: "run-1",
			occurred_at: "2026-09-02T12:00:00Z",
			kind: "delta",
			status: "running",
			payload: { type: "delta", content: "hello" },
		};

		(
			webSocketService as unknown as {
				handleMessage(message: unknown): void;
			}
		).handleMessage(event);

		expect(callback).toHaveBeenCalledWith(event);
		unsubscribe();
	});

	it("reopens a closed socket even when the chat channel is remembered", async () => {
		const service = webSocketService as unknown as {
			ws: WebSocket | null;
			subscribedChannels: Set<string>;
			connect(channels: string[]): Promise<void>;
			connectToChat(conversationId: string): Promise<void>;
		};
		const channel = "chat:conversation-reconnect";
		service.ws = null;
		service.subscribedChannels.add(channel);
		const connect = vi
			.spyOn(service, "connect")
			.mockResolvedValue(undefined);

		await service.connectToChat("conversation-reconnect");

		expect(connect).toHaveBeenCalledWith([channel]);
		service.subscribedChannels.delete(channel);
		connect.mockRestore();
	});

	it("does not dispatch unversioned direct chat chunks", () => {
		const callback = vi.fn();
		const unsubscribe = webSocketService.onChatStream(
			"conversation-strict",
			callback,
		);

		(
			webSocketService as unknown as {
				handleMessage(message: unknown): void;
			}
		).handleMessage({
			type: "delta",
			conversation_id: "conversation-strict",
			content: "legacy",
		});

		expect(callback).not.toHaveBeenCalled();
		unsubscribe();
	});
});

describe("service log streaming contract", () => {
	it("dispatches bridged attempt lines by service id", () => {
		const callback = vi.fn();
		const unsubscribe = webSocketService.onServiceLog(
			"svc-1",
			callback,
		);

		(
			webSocketService as unknown as {
				handleMessage(message: unknown): void;
			}
		).handleMessage({
			type: "service_log",
			service_id: "svc-1",
			attempt_id: "att-1",
			level: "INFO",
			message: "bridged live",
			timestamp: "2026-09-21T12:00:00+00:00",
		});

		expect(callback).toHaveBeenCalledOnce();
		expect(callback).toHaveBeenCalledWith({
			serviceId: "svc-1",
			attemptId: "att-1",
			timestamp: "2026-09-21T12:00:00+00:00",
			level: "INFO",
			message: "bridged live",
		});
		unsubscribe();
	});

	it("does not leak lines across services", () => {
		const callback = vi.fn();
		const unsubscribe = webSocketService.onServiceLog(
			"svc-1",
			callback,
		);

		(
			webSocketService as unknown as {
				handleMessage(message: unknown): void;
			}
		).handleMessage({
			type: "service_log",
			service_id: "svc-2",
			attempt_id: "att-9",
			level: "INFO",
			message: "other service",
			timestamp: "2026-09-21T12:00:00+00:00",
		});

		expect(callback).not.toHaveBeenCalled();
		unsubscribe();
	});
});
