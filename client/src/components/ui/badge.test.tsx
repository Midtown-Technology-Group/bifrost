/**
 * Pins the Badge theme-token contract: no variant may use raw Tailwind
 * palette colors (bg-amber-500, text-blue-700, ...). Those bypass the
 * vendored Bifrost design-system tokens and dark-mode overrides.
 *
 * The `warning` variant regressed to raw amber once; it now uses the
 * `--color-warning` theme token (mapped to `--bf-warning`, which carries
 * its own dark-mode value, so no `dark:` overrides are needed).
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { Badge, badgeVariants } from "./badge";

// Matches raw palette color segments such as `amber-500` in `bg-amber-500/15`
// or `dark:text-amber-400`. Theme tokens (primary, destructive, warning,
// chart-1, muted, ...) never match.
const RAW_PALETTE =
	/(bg|text|border|fill|stroke|ring)-(red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|gray|zinc|neutral|stone)-\d{2,3}/;

const VARIANTS = [
	"default",
	"secondary",
	"destructive",
	"outline",
	"ghost",
	"link",
	"warning",
] as const;

describe("Badge theme-token contract", () => {
	it("warning variant uses the warning theme token", () => {
		render(<Badge variant="warning">Needs review</Badge>);
		const badge = screen.getByText("Needs review");
		const classes = badge.className.split(/\s+/);
		expect(classes).toContain("bg-warning/15");
		expect(classes).toContain("text-warning");
		expect(classes).toContain("[a]:hover:bg-warning/25");
	});

	it("no variant uses raw Tailwind palette colors", () => {
		for (const variant of VARIANTS) {
			expect(badgeVariants({ variant })).not.toMatch(RAW_PALETTE);
		}
	});
});
