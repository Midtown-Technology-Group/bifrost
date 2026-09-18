import { describe, expect, it } from "vitest";
import { collectCallbackUrlParams } from "./oauth-callback-params";

describe("collectCallbackUrlParams", () => {
	it("forwards provider params such as realmId", () => {
		const params = collectCallbackUrlParams(
			new URLSearchParams("code=abc&state=xyz&realmId=9130350000000000"),
		);

		expect(params).toEqual({ realmId: "9130350000000000" });
	});

	it("excludes protocol params consumed by the callback flow", () => {
		const params = collectCallbackUrlParams(
			new URLSearchParams(
				"code=abc&state=xyz&error=access_denied&error_description=nope&realmId=1",
			),
		);

		expect(params).toEqual({ realmId: "1" });
	});

	it("returns an empty object when only protocol params are present", () => {
		expect(
			collectCallbackUrlParams(new URLSearchParams("code=a&state=b")),
		).toEqual({});
	});

	it("preserves __proto__ as an own property without polluting the prototype", () => {
		const params = collectCallbackUrlParams(
			new URLSearchParams("__proto__=polluted&realmId=1"),
		);

		const descriptor = Object.getOwnPropertyDescriptor(params, "__proto__");
		expect(descriptor?.value).toBe("polluted");
		expect(descriptor?.enumerable).toBe(true);
		expect(params.realmId).toBe("1");
		expect(Object.getPrototypeOf(params)).toBe(Object.prototype);
	});
});
