import { RouterContextProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

const { prepareAppBundle } = vi.hoisted(() => ({
	prepareAppBundle: vi.fn().mockResolvedValue(undefined),
}));
const { authFetch } = vi.hoisted(() => ({
	authFetch: vi.fn(),
}));

vi.mock("@/components/jsx-app/BundledAppShell", () => ({
	prepareAppBundle,
}));

vi.mock("@/lib/api-client", async () => {
	const actual = await vi.importActual<typeof import("@/lib/api-client")>(
		"@/lib/api-client",
	);
	return { ...actual, authFetch };
});

import { queryClient } from "@/lib/queryClient";
import {
	agentDetailLoader,
	applicationDetailLoader,
	prefetchApplicationDetail,
} from "./detail-route-loaders";

class LoadedImage {
	complete = false;
	onload: (() => void) | null = null;
	onerror: (() => void) | null = null;

	set src(_value: string) {
		queueMicrotask(() => this.onload?.());
	}
}

describe("detail route loaders", () => {
	afterEach(() => {
		vi.restoreAllMocks();
		prepareAppBundle.mockClear();
		authFetch.mockReset();
		vi.unstubAllGlobals();
	});

	it("rejects preview loading before application or draft bundle reads for non-admins", async () => {
		authFetch.mockResolvedValue(
			new Response(JSON.stringify({ is_superuser: false }), {
				status: 200,
			}),
		);
		const ensure = vi.spyOn(queryClient, "ensureQueryData");
		const request = new Request("http://localhost/apps/portal/preview");

		const result = applicationDetailLoader(true)({
			params: { applicationId: "portal", "*": "" },
			request,
			url: new URL(request.url),
			pattern: "/apps/:applicationId/preview/*",
			context: new RouterContextProvider(),
		});

		await expect(result).rejects.toMatchObject({ status: 403 });
		expect(authFetch).toHaveBeenCalledWith("/auth/me", {
			signal: request.signal,
		});
		expect(ensure).not.toHaveBeenCalled();
		expect(prepareAppBundle).not.toHaveBeenCalled();
	});

	it("waits for agent metadata and its primary image", async () => {
		vi.stubGlobal("Image", LoadedImage);
		const ensure = vi
			.spyOn(queryClient, "ensureQueryData")
			.mockResolvedValue({ logo_url: "/agent-logo.png" });

		const request = new Request("http://localhost/agents/agent-1");
		await agentDetailLoader({
			params: { id: "agent-1" },
			request,
			url: new URL(request.url),
			pattern: "/agents/:id",
			context: new RouterContextProvider(),
		});

		expect(ensure).toHaveBeenCalledOnce();
		expect(ensure.mock.calls[0]?.[0].queryKey).toEqual([
			"get",
			"/api/agents/{agent_id}",
			{ params: { path: { agent_id: "agent-1" } } },
		]);
	});

	it("waits for application metadata, image, and bundle readiness", async () => {
		vi.stubGlobal("Image", LoadedImage);
		vi.spyOn(queryClient, "ensureQueryData").mockResolvedValue({
			id: "app-1",
			slug: "portal",
			is_published: true,
			logo_url: "/app-logo.png",
		});
		const request = new Request("http://localhost/apps/portal");

		await applicationDetailLoader(false)({
			params: { applicationId: "portal", "*": "" },
			request,
			url: new URL(request.url),
			pattern: "/apps/:applicationId/*",
			context: new RouterContextProvider(),
		});

		expect(prepareAppBundle).toHaveBeenCalledWith({
			appId: "app-1",
			isPreview: false,
			signal: request.signal,
		});
	});
});

it("does not replace newer application detail with a card snapshot", () => {
	vi.spyOn(queryClient, "getQueryData").mockReturnValue({
		name: "Updated remotely",
	});
	const set = vi.spyOn(queryClient, "setQueryData");
	prefetchApplicationDetail(
		{
			id: "fixture",
			slug: "fixture",
			name: "Old list name",
			logo_url: null,
		} as Parameters<typeof prefetchApplicationDetail>[0],
		false,
	);
	expect(set).not.toHaveBeenCalled();
	vi.restoreAllMocks();
});

it.each([false, true])(
	"only prepares unpublished apps in preview mode (%s)",
	async (preview) => {
		authFetch.mockResolvedValue(
			new Response(JSON.stringify({ is_superuser: true }), {
				status: 200,
			}),
		);
		vi.spyOn(queryClient, "ensureQueryData").mockResolvedValue({
			id: "unpublished",
			slug: "unpublished",
			is_published: false,
		});
		prepareAppBundle.mockClear();
		const request = new Request("http://localhost/apps/unpublished");
		await applicationDetailLoader(preview)({
			params: { applicationId: "unpublished" },
			request,
			url: new URL(request.url),
			pattern: "/apps/:applicationId/*",
			context: new RouterContextProvider(),
		});
		expect(prepareAppBundle).toHaveBeenCalledTimes(preview ? 1 : 0);
		vi.restoreAllMocks();
	},
);
