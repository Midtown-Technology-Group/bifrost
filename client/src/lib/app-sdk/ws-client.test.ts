import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setBifrostTransport } from "./tables";
import type { TableChangeMessage } from "./ws-client";
import { buildWsUrl, subscribeToTable } from "./ws-client";

type MockEvent = { data?: string; code?: number };

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  sent: string[] = [];
  listeners: Record<string, ((event: MockEvent) => void)[]> = {};

  constructor(public url: string) {
    MockWebSocket.instances.push(this);
  }

  addEventListener(type: string, callback: (event: MockEvent) => void) {
    (this.listeners[type] ??= []).push(callback);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.emit("close", { code: 1000 });
  }

  emit(type: string, event: MockEvent) {
    for (const callback of this.listeners[type] ?? []) callback(event);
  }
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.useFakeTimers();
  vi.stubGlobal("crypto", {
    getRandomValues: (value: Uint32Array) => {
      value[0] = 0x80000000;
      return value;
    },
  });
  vi.stubGlobal("WebSocket", MockWebSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("subscribeToTable", () => {
  it("dispatches table invalidation frames to the subscriber", () => {
    const events: TableChangeMessage[] = [];
    const invalidation: TableChangeMessage = {
      type: "table_invalidated",
      table_id: "table-1",
    };
    const unsubscribe = subscribeToTable("table-1", null, (event) => {
      events.push(event);
    });

    MockWebSocket.instances[0].emit("message", {
      data: JSON.stringify(invalidation),
    });

    expect(events).toEqual([invalidation]);
    unsubscribe();
  });

  it("reconnects, resubscribes, and signals snapshot recovery after acknowledgement", () => {
    const events: Record<string, unknown>[] = [];
    const recovered = vi.fn();
    const unsubscribe = subscribeToTable(
      "table-1",
      { eq: [{ row: "status" }, "active"] },
      (event) => events.push(event),
      recovered,
    );

    const first = MockWebSocket.instances[0];
    first.emit("open", {});
    first.emit("message", {
			data: JSON.stringify({
				type: "subscribed",
				channel: "table:table-1",
			}),
    });
    expect(recovered).not.toHaveBeenCalled();

    first.emit("close", { code: 1012 });
    vi.advanceTimersByTime(500);

    const second = MockWebSocket.instances[1];
    second.emit("open", {});
    expect(JSON.parse(second.sent[0])).toEqual({
      type: "subscribe",
      channels: [
        {
          name: "table:table-1",
          filter: { eq: [{ row: "status" }, "active"] },
        },
      ],
    });
    expect(recovered).not.toHaveBeenCalled();

    second.emit("message", {
			data: JSON.stringify({
				type: "subscribed",
				channel: "table:table-1",
			}),
    });
    expect(recovered).toHaveBeenCalledTimes(1);
    expect(events).toHaveLength(2);
    unsubscribe();
  });

  it("cancels a scheduled reconnect when the caller unsubscribes", () => {
    const unsubscribe = subscribeToTable("table-1", null, () => {});
    MockWebSocket.instances[0].emit("close", { code: 1006 });

    unsubscribe();
    vi.advanceTimersByTime(60_000);

    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("requests a snapshot refresh when the initial handshake only succeeds after retry", () => {
    const recovered = vi.fn();
		const unsubscribe = subscribeToTable(
			"table-1",
			null,
			() => {},
			recovered,
		);
    MockWebSocket.instances[0].emit("close", { code: 1006 });

    vi.advanceTimersByTime(500);
    MockWebSocket.instances[1].emit("message", {
			data: JSON.stringify({
				type: "subscribed",
				channel: "table:table-1",
			}),
    });

    expect(recovered).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("treats an authorization close as terminal", () => {
    const unsubscribe = subscribeToTable("table-1", null, () => {});
    MockWebSocket.instances[0].emit("close", { code: 4003 });

    vi.advanceTimersByTime(60_000);

    expect(MockWebSocket.instances).toHaveLength(1);
    unsubscribe();
  });

  it("exchanges the live bearer for a fresh ticket before opening", async () => {
    const ticket = "a".repeat(43);
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ticket }),
    });
    const restore = setBifrostTransport({
      baseUrl: "https://remote.example",
      getToken: () => "live-bearer",
      fetchImpl,
    });

    const unsubscribe = subscribeToTable("table-1", null, () => {});
    try {
      for (let turn = 0; turn < 5; turn += 1) await Promise.resolve();

      expect(fetchImpl).toHaveBeenCalledWith(
        "https://remote.example/ws/ticket",
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: "Bearer live-bearer",
          }),
        }),
      );
      expect(MockWebSocket.instances).toHaveLength(1);
      const url = new URL(MockWebSocket.instances[0].url);
      expect(url.searchParams.get("ticket")).toBe(ticket);
      expect(url.searchParams.has("token")).toBe(false);
    } finally {
      unsubscribe();
      restore();
    }
  });
});

describe("buildWsUrl", () => {
  let restore: (() => void) | null = null;

  afterEach(() => {
    restore?.();
    restore = null;
  });

  it("never derives a query credential from the transport bearer token", () => {
    restore = setBifrostTransport({
      baseUrl: "https://remote.example",
      token: "tok",
    });
    expect(buildWsUrl()).toBe("wss://remote.example/ws/connect");
  });

  it("places only an opaque single-use ticket in the connection URL", () => {
    restore = setBifrostTransport({ baseUrl: "https://remote.example" });
    const url = new URL(buildWsUrl("a".repeat(43)));
    expect(url.searchParams.get("ticket")).toBe("a".repeat(43));
    expect(url.searchParams.has("token")).toBe(false);
  });

  it("defaults to the window origin with NO token param (v1 inline, cookie auth)", () => {
    const url = new URL(buildWsUrl());
    const origin = new URL(window.location.href);
		expect(url.protocol).toBe(
			origin.protocol === "https:" ? "wss:" : "ws:",
		);
    expect(url.host).toBe(origin.host);
    expect(url.pathname).toBe("/ws/connect");
    expect(url.searchParams.has("token")).toBe(false);
  });

  it("maps an http baseUrl to the ws: scheme", () => {
    restore = setBifrostTransport({
      baseUrl: "http://localhost:8000",
      token: "t2",
    });
    expect(buildWsUrl()).toBe("ws://localhost:8000/ws/connect");
  });

	it("does not call the live token reader while building a URL", () => {
		let token = "t-old";
		const getToken = vi.fn(() => token);
		restore = setBifrostTransport({
			baseUrl: "https://remote.example",
			token: "bootstrap-token",
			getToken,
		});

		expect(buildWsUrl()).toBe("wss://remote.example/ws/connect");
		token = "t-new";
		expect(buildWsUrl()).toBe("wss://remote.example/ws/connect");
		expect(getToken).not.toHaveBeenCalled();
	});
});
