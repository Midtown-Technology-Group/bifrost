import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import config from "../vitest.config.ts";

const clientRoot = path.resolve(import.meta.dirname, "..");

// Vitest 4 always excludes vite/vitest config files from its coverage provider.
// Native Node imports this actual TS file and includes its execution in LCOV.
describe("Sonar coverage configuration", () => {
	it("includes unimported TypeScript and TSX implementation files", () => {
		const coverage = config.test?.coverage;
		assert.ok(coverage && typeof coverage === "object");
		assert.equal(coverage.provider, "v8");
		assert.deepEqual(coverage.include, ["src/**/*.{ts,tsx}"]);
		assert.deepEqual(coverage.exclude, [
			"src/**/*.d.ts",
			"src/**/*.{test,spec}.{ts,tsx}",
			"src/test/**",
		]);
		assert.ok(
			coverage.reporter.some(
				(reporter) =>
					Array.isArray(reporter) &&
					reporter[0] === "lcovonly" &&
					reporter[1].projectRoot === path.resolve(clientRoot, ".."),
			),
		);
		assert.equal(config.resolve.alias["@"], path.join(clientRoot, "src"));
	});

	it("locks the coverage provider to the installed Vitest version", () => {
		const lock = JSON.parse(
			readFileSync(path.join(clientRoot, "package-lock.json"), "utf8"),
		);
		const version = lock.packages["node_modules/vitest"].version;
		assert.equal(
			lock.packages["node_modules/@vitest/coverage-v8"].version,
			version,
		);
		assert.equal(
			lock.packages[""].devDependencies["@vitest/coverage-v8"],
			version,
		);
	});
});
