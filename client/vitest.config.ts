/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
	plugins: [react()],
	resolve: {
		alias: {
			"@": path.resolve(import.meta.dirname, "./src"),
		},
	},
	test: {
		environment: "happy-dom",
		globals: true,
		setupFiles: ["./src/test/setup.ts"],
		include: ["src/**/*.test.{ts,tsx}"],
		// Exclude Playwright e2e specs so they don't accidentally run here
		exclude: ["e2e/**", "node_modules/**", "dist/**"],
		// happy-dom suites are CPU-heavy, and this repository commonly runs
		// several worktree test stacks at once. Keep an absolute cap so larger
		// hosts do not turn that shared load into interaction-test timeouts.
		maxWorkers: 2,
		coverage: {
			provider: "v8",
			// Vitest 4 needs an explicit include to count unimported source files.
			include: ["src/**/*.{ts,tsx}"],
			exclude: [
				// Declarations have no executable code; v1.d.ts is generated.
				"src/**/*.d.ts",
				"src/**/*.{test,spec}.{ts,tsx}",
				// Test setup and test-only helpers are not application source.
				"src/test/**",
			],
			reportsDirectory: "./coverage",
			reporter: [
				"text-summary",
				// Sonar runs from the repository root, not the client directory.
				[
					"lcovonly",
					{ projectRoot: path.resolve(import.meta.dirname, "..") },
				],
				"json-summary",
			],
		},
		css: false,
	},
});
