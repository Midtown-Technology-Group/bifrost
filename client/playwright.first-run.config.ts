import { defineConfig, devices } from "@playwright/test";

// Run through ./test.sh client e2e --config=playwright.first-run.config.ts.
// The normal global setup creates users before tests, so bootstrap needs its
// own fresh-database journey. No retries or authenticated storage state.
export default defineConfig({
	testDir: "./e2e/preview",
	testMatch: "first-user-setup.unauth.spec.ts",
	outputDir: "./playwright-results/first-run",
	retries: 0,
	workers: 1,
	forbidOnly: true,
	use: {
		...devices["Desktop Chrome"],
		baseURL: process.env.TEST_BASE_URL || "http://localhost:3000",
		trace: "retain-on-failure",
		screenshot: "only-on-failure",
	},
});
