import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const post = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api-client", () => ({
	apiClient: { POST: post },
}));

import { useFormFileUpload } from "./useFormFileUpload";

class MockXMLHttpRequest {
	static latest: MockXMLHttpRequest;
	status = 204;
	upload = { addEventListener: vi.fn() };
	open = vi.fn();
	setRequestHeader = vi.fn();
	private listeners = new Map<string, () => void>();

	constructor() {
		MockXMLHttpRequest.latest = this;
	}

	addEventListener(name: string, listener: () => void) {
		this.listeners.set(name, listener);
	}

	send() {
		this.listeners.get("load")?.();
	}
}

beforeEach(() => {
	post.mockReset().mockResolvedValue({
		data: {
			upload_url: "/api/forms/form-1/upload",
			upload_headers: {
				Authorization: "Bearer bounded-upload-token",
				"Content-Type": "application/pdf",
			},
			blob_uri: "form-1/session/file/report.pdf",
			expires_at: "2030-01-01T00:00:00Z",
			file_metadata: {
				name: "report.pdf",
				container: "uploads",
				path: "form-1/session/file/report.pdf",
				content_type: "application/pdf",
				size: 3,
			},
		},
		error: undefined,
	});
	vi.stubGlobal("XMLHttpRequest", MockXMLHttpRequest);
});

it("applies every server-authorized upload header", async () => {
	const { result } = renderHook(() =>
		useFormFileUpload("form-1", { fieldName: "attachment" }),
	);
	const file = new File(["pdf"], "report.pdf", {
		type: "application/pdf",
	});

	await act(async () => {
		await result.current.uploadFile(file);
	});

	expect(MockXMLHttpRequest.latest.open).toHaveBeenCalledWith(
		"PUT",
		"/api/forms/form-1/upload",
	);
	expect(MockXMLHttpRequest.latest.setRequestHeader).toHaveBeenCalledWith(
		"Authorization",
		"Bearer bounded-upload-token",
	);
	expect(MockXMLHttpRequest.latest.setRequestHeader).toHaveBeenCalledWith(
		"Content-Type",
		"application/pdf",
	);
});
