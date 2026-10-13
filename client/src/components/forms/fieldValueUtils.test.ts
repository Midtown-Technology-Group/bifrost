import { describe, expect, it } from "vitest";
import { normalizeCheckboxDefault } from "./fieldValueUtils";

describe("normalizeCheckboxDefault", () => {
	it.each([
		[true, true],
		[false, false],
		["true", true],
		[" TRUE ", true],
		["false", false],
		["yes", false],
		[1, true],
		[0, false],
	])("normalizes %p to %p", (value, expected) => {
		expect(normalizeCheckboxDefault(value)).toBe(expected);
	});
});
